"""Async HTTP core for fetch providers.

Every provider passes an injected ``httpx.AsyncClient`` into these helpers.
The module enforces bounded response reads, redacted logging, host-scoped
concurrency, transient retry, and shared status-to-error translation.
"""

from __future__ import annotations

import asyncio
import json as json_lib
from dataclasses import dataclass
from typing import Any, overload, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from omnifetch.fetch.shared.config import HttpSettings
from omnifetch.fetch.shared.types import ErrorType, ProviderError
from omnifetch.fetch.shared.util import handle_rate_limit
from omnifetch.logging import get_logger

_LOGGER = get_logger("fetch.http")
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_DEFAULT_HTTP_SETTINGS = HttpSettings()
_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500
_SENSITIVE_QUERY_PARAMS = frozenset(
    {"api_key", "key", "token", "app_id", "x-api-key", "apikey"}
)
_HOST_SEMAPHORES: dict[tuple[str, int], asyncio.Semaphore] = {}

_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class _RequestOptions:
    """Keyword options passed through to ``httpx.AsyncClient.stream``."""

    method: str = "GET"
    headers: dict[str, str] | None = None
    content: str | bytes | None = None
    json: Any = None
    timeout_s: float | None = None
    expected_statuses: tuple[int, ...] = ()
    http_settings: HttpSettings = _DEFAULT_HTTP_SETTINGS


def _host_semaphore(url: str, http_settings: HttpSettings) -> asyncio.Semaphore:
    """Return the host-scoped concurrency semaphore for ``url``."""
    host = urlsplit(url).hostname or ""
    limit = http_settings.limit_per_host
    key = (host, limit)
    semaphore = _HOST_SEMAPHORES.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(limit)
        _HOST_SEMAPHORES[key] = semaphore
    return semaphore


def _is_transient_error(error: BaseException) -> bool:
    """Return whether the error is eligible for HTTP-level retry."""
    return (
        isinstance(error, ProviderError)
        and error.error_type is ErrorType.PROVIDER_ERROR
    )


def _redact(url: str) -> str:
    """Redact sensitive query parameters in a URL for safe logs."""
    try:
        parts = urlsplit(url)
        query_items = (
            (
                key,
                "[REDACTED]"
                if key.lower() in _SENSITIVE_QUERY_PARAMS
                else value,
            )
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        )
        return urlunsplit(parts._replace(query=urlencode(tuple(query_items))))[
            :200
        ]
    except ValueError:
        return url[:200]


async def _read_capped(response: httpx.Response, provider: str) -> str:
    """Read a response stream while enforcing the maximum byte count."""
    try:
        content_length = int(response.headers.get("content-length") or "0")
    except ValueError:
        content_length = 0
    if content_length > _MAX_RESPONSE_BYTES:
        await response.aclose()
        raise ProviderError(
            ErrorType.API_ERROR,
            f"Response too large ({content_length} bytes)",
            provider,
        )

    total_bytes = 0
    chunks: list[bytes] = []
    async for chunk in response.aiter_bytes():
        total_bytes += len(chunk)
        if total_bytes > _MAX_RESPONSE_BYTES:
            await response.aclose()
            raise ProviderError(
                ErrorType.API_ERROR,
                f"Response too large (>{_MAX_RESPONSE_BYTES} bytes)",
                provider,
            )
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _parse_message(raw: str, fallback: str) -> str:
    """Extract a safe provider error message from a response body."""
    if not raw:
        return fallback
    try:
        payload = json_lib.loads(raw)
    except json_lib.JSONDecodeError:
        return fallback
    if not isinstance(payload, dict):
        return fallback
    for key in ("message", "error", "detail"):
        value = payload.get(key)
        if isinstance(value, str):
            return value[:200]
    return fallback


def _raise_for_status(
    provider: str,
    status: int,
    raw: str,
    expected_statuses: tuple[int, ...],
    reason_phrase: str,
) -> None:
    """Translate HTTP status outcomes into provider error taxonomy."""
    if _HTTP_OK_MIN <= status < _HTTP_OK_MAX or status in expected_statuses:
        return

    message = _parse_message(raw, reason_phrase)
    _LOGGER.warning(
        "HTTP error response provider=%s status=%s message=%s",
        provider,
        status,
        message,
    )
    if status == _HTTP_UNAUTHORIZED:
        raise ProviderError(ErrorType.API_ERROR, "Invalid API key", provider)
    if status == _HTTP_FORBIDDEN:
        raise ProviderError(
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
            provider,
        )
    if status == _HTTP_TOO_MANY_REQUESTS:
        handle_rate_limit(provider)
    if status >= _HTTP_SERVER_ERROR_MIN:
        raise ProviderError(
            ErrorType.PROVIDER_ERROR,
            f"{provider} API internal error ({status}): {message}",
            provider,
        )
    raise ProviderError(
        ErrorType.API_ERROR,
        f"{provider} error ({status}): {message}",
        provider,
    )


async def _do_request(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    options: _RequestOptions,
) -> tuple[str, int]:
    """Perform one HTTP attempt and map outcomes to ``ProviderError``."""
    _LOGGER.debug("HTTP %s %s", options.method, _redact(url))
    timeout = (
        options.timeout_s
        if options.timeout_s is not None
        else httpx.USE_CLIENT_DEFAULT
    )
    try:
        async with client.stream(
            options.method,
            url,
            headers=options.headers,
            content=options.content,
            json=options.json,
            timeout=timeout,
        ) as response:
            raw = await _read_capped(response, provider)
            status = response.status_code
            reason_phrase = response.reason_phrase
    except httpx.HTTPError as error:
        raise ProviderError(
            ErrorType.PROVIDER_ERROR, str(error), provider
        ) from error

    _raise_for_status(
        provider, status, raw, options.expected_statuses, reason_phrase
    )
    return raw, status


async def _request(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    **kwargs: Any,
) -> tuple[str, int]:
    """Run one host-capped request with optional transient retry."""
    options = _RequestOptions(**kwargs)
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_transient_error),
        stop=stop_after_attempt(1 + options.http_settings.transient_retries),
        wait=wait_exponential_jitter(initial=0.25, max=2.0),
        reraise=True,
    ):
        with attempt:
            async with _host_semaphore(url, options.http_settings):
                return await _do_request(client, provider, url, options)
    raise RuntimeError("request retry helper exhausted")  # pragma: no cover


@overload  # pragma: no cover
async def http_json(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    *,
    model: type[_ResponseModel],
    **kwargs: Any,
) -> _ResponseModel: ...  # pragma: no cover


@overload  # pragma: no cover
async def http_json(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    *,
    model: None = None,
    **kwargs: Any,
) -> Any: ...  # pragma: no cover


async def http_json(
    client: httpx.AsyncClient,
    provider: str,
    url: str,
    *,
    model: type[_ResponseModel] | None = None,
    **kwargs: Any,
) -> Any:
    """Return a JSON response, optionally validated as a Pydantic model."""
    raw, _ = await _request(client, provider, url, **kwargs)
    try:
        data = json_lib.loads(raw)
    except json_lib.JSONDecodeError as error:
        raise ProviderError(
            ErrorType.API_ERROR,
            f"Invalid JSON response from {provider}",
            provider,
        ) from error
    return model.model_validate(data) if model is not None else data


async def http_text(
    client: httpx.AsyncClient, provider: str, url: str, **kwargs: Any
) -> str:
    """Return a text response body."""
    raw, _ = await _request(client, provider, url, **kwargs)
    return raw


async def http_raw(
    client: httpx.AsyncClient, provider: str, url: str, **kwargs: Any
) -> tuple[str, int]:
    """Return a text response body and HTTP status code."""
    return await _request(client, provider, url, **kwargs)
