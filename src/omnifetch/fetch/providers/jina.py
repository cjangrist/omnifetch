"""Jina Reader fetch provider: URL to clean markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "JINA_API_KEY"
_TIMEOUT_MS = 30_000
_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300
_HTTP_TOO_MANY_REQUESTS = 429


class _JinaUsage(BaseModel):
    """Typed subset of Jina Reader usage metadata."""

    model_config = ConfigDict(extra="ignore")

    tokens: int | None = None


class _JinaData(BaseModel):
    """Typed subset of Jina Reader response data."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    url: str | None = None
    content: str | None = None
    usage: _JinaUsage | None = None


class _JinaReaderResponse(BaseModel):
    """Typed subset of Jina Reader responses."""

    model_config = ConfigDict(extra="ignore")

    code: int | None = None
    data: _JinaData | None = None


def _metadata_from_usage(usage: _JinaUsage | None) -> dict[str, int] | None:
    """Return token metadata when Jina reports nonzero usage."""
    if usage is None:
        return None
    if not usage.tokens:
        return None
    return {"tokens": usage.tokens}


def _raise_for_application_code(
    response: _JinaReaderResponse, provider: str
) -> None:
    """Map Jina response-envelope status codes to provider errors."""
    code = response.code
    if code is None or _HTTP_OK_MIN <= code < _HTTP_OK_MAX:
        return
    if code == _HTTP_TOO_MANY_REQUESTS:
        raise ProviderError(
            ErrorType.RATE_LIMIT,
            f"Rate limit exceeded for {provider}",
            provider,
        )
    raise ProviderError(
        ErrorType.API_ERROR,
        f"Jina API error (code={code})",
        provider,
    )


class JinaFetchProvider(FetchProvider):
    """Fetch clean markdown using Jina Reader API."""

    name = "jina"
    description = (
        "Read a URL as markdown using Jina Reader API. Fast and "
        "token-efficient."
    )
    base_url = "https://r.jina.ai"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Jina Reader and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            response = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/",
                model=_JinaReaderResponse,
                method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Return-Format": "markdown",
                },
                json={"url": url},
                timeout_s=self.timeout_s,
            )
            _raise_for_application_code(response, self.name)
            if response.data is None:
                raise ValueError("Jina Reader returned no content")
            if not response.data.content:
                raise ValueError("Jina Reader returned no content")

            return FetchResult(
                url=response.data.url or url,
                title=response.data.title or "",
                content=response.data.content,
                source_provider=self.name,
                metadata=_metadata_from_usage(response.data.usage),
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
