"""Stateless helpers shared across the fetch engine.

This module centralizes API-key validation, cache-key hashing, auth encoding,
error normalization, log sanitization, retry policy, and provider timeouts.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import NoReturn, TypeVar
from urllib.parse import urlsplit

from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random,
)

from omnifetch.fetch.shared.types import ErrorType, ProviderError

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_WRAPPING_QUOTES = re.compile(r"""^(['"])(.*)\1$""", re.DOTALL)
_URL_TOKEN = re.compile(r"https?://\S+")
_NOT_FOUND_ERROR_PATTERN = re.compile(
    r"\b404\s+not\s+found\b"
    r"|"
    r"\b(?:target|url|page)\b.{0,80}"
    r"(?:\bnot found\b|\b404\b)"
    r"|"
    r"(?:\bnot found\b|\b404\b).{0,80}"
    r"\b(?:target|url|page)\b"
    r"|"
    r"\bhttp\s+status\s+404\b.{0,80}\b(?:target|url|page)\b"
    r"|"
    r"\bserver\b.{0,32}\breturned\b.{0,16}\b404\b"
    r"|"
    r"\bcould\s+not\s+find\b.{0,80}\b(?:target|url|page)\b",
    re.IGNORECASE,
)
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_MIN_TIMEOUT_MS = 2000
_DEFAULT_MAX_TIMEOUT_MS = 5000

_RetryResult = TypeVar("_RetryResult")


def validate_api_key(key: str | None, provider: str) -> str:
    """Return a trimmed and unquoted API key, or raise ``ProviderError``."""
    if not key:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"API key not found for {provider}",
            provider,
        )
    trimmed_key = key.strip()
    quote_match = _WRAPPING_QUOTES.match(trimmed_key)
    return quote_match.group(2) if quote_match else trimmed_key


def hash_key(prefix: str, value: str) -> str:
    """Return a prefixed SHA-256 hex digest for cache-safe keys."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def basic_auth(username: str, password: str = "") -> str:
    """Return base64-encoded ``username:password`` for Basic auth headers."""
    return base64.b64encode(f"{username}:{password}".encode()).decode()


def timing_safe_equal(first_value: str, second_value: str) -> bool:
    """Return whether two strings are equal using constant-time comparison."""
    first_bytes = first_value.encode()
    second_bytes = second_value.encode()
    return len(first_bytes) == len(second_bytes) and hmac.compare_digest(
        first_bytes, second_bytes
    )


def sanitize_for_log(text: str) -> str:
    """Strip control characters and clamp text to 200 characters."""
    return _CONTROL_CHARACTERS.sub("", text)[:200]


def _target_url_variants(target_url: str) -> tuple[str, ...]:
    """Return target URL forms providers commonly echo in messages."""
    if not target_url:
        return ()

    variants = {target_url}
    try:
        parts = urlsplit(target_url)
    except ValueError:
        return tuple(variants)

    host_path = f"{parts.netloc}{parts.path}"
    if host_path:
        variants.add(host_path)
        if parts.query:
            variants.add(f"{host_path}?{parts.query}")
    return tuple(sorted(variants, key=len, reverse=True))


def _strip_target_url_variants(message: str, target_url: str | None) -> str:
    """Remove exact and schemeless target URL echoes from a message."""
    if target_url is None:
        return message
    stripped_message = message
    for variant in _target_url_variants(target_url):
        stripped_message = re.sub(
            re.escape(variant),
            " ",
            stripped_message,
            flags=re.IGNORECASE,
        )
    return stripped_message


def is_not_found_error_message(
    message: str,
    target_url: str | None = None,
) -> bool:
    """Return whether a provider message indicates a missing target."""
    without_target_url = _strip_target_url_variants(message, target_url)
    searchable_message = _URL_TOKEN.sub(" ", without_target_url)
    return _NOT_FOUND_ERROR_PATTERN.search(searchable_message) is not None


def handle_rate_limit(provider: str, reset_time: str | None = None) -> None:
    """Raise a ``RATE_LIMIT`` provider error with optional reset metadata."""
    reset_suffix = f". Reset at {reset_time}" if reset_time else ""
    raise ProviderError(
        ErrorType.RATE_LIMIT,
        f"Rate limit exceeded for {provider}{reset_suffix}",
        provider,
        {"reset_time": reset_time} if reset_time else None,
    )


def handle_provider_error(
    error: Exception, provider: str, operation: str = "operation"
) -> NoReturn:
    """Re-raise provider errors and wrap other exceptions as ``API_ERROR``."""
    if isinstance(error, ProviderError):
        raise error
    raise ProviderError(
        ErrorType.API_ERROR,
        f"Failed to {operation}: {error}",
        provider,
    ) from error


def create_error_response(error: Exception) -> dict[str, str]:
    """Return the structured error object used by fetch tool error paths."""
    if isinstance(error, ProviderError):
        return {"error": f"{error.provider} error: {error}"}
    return {"error": f"Unexpected error: {error}"}


@contextlib.asynccontextmanager
async def provider_timeout(timeout_ms: int) -> AsyncIterator[None]:
    """Bound a provider attempt by its configured deadline in milliseconds."""
    async with asyncio.timeout(timeout_ms / 1000):
        yield


def _is_retryable_error(error: BaseException) -> bool:
    """Return whether Tenacity should retry the given exception."""
    if isinstance(error, ProviderError):
        return error.error_type is ErrorType.PROVIDER_ERROR
    return True


async def retry_with_backoff(
    function: Callable[[], Awaitable[_RetryResult]],
    max_retries: int = _DEFAULT_MAX_RETRIES,
    min_timeout_ms: int = _DEFAULT_MIN_TIMEOUT_MS,
    max_timeout_ms: int = _DEFAULT_MAX_TIMEOUT_MS,
) -> _RetryResult:
    """Retry an async function with transient-only randomized backoff."""
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_retryable_error),
        stop=stop_after_attempt(max_retries + 1),
        wait=wait_random(min_timeout_ms / 1000, max_timeout_ms / 1000),
        reraise=True,
    ):
        with attempt:
            return await function()
    raise RuntimeError("retry helper exhausted")  # pragma: no cover
