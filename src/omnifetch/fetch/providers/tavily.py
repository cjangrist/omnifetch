"""Tavily fetch provider: URL to markdown through Extract API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    is_not_found_error_message,
    validate_api_key,
)

_API_KEY_ENV_NAME = "TAVILY_API_KEY"
_TIMEOUT_MS = 30_000


class _TavilyResult(BaseModel):
    """Successful Tavily extraction result."""

    model_config = ConfigDict(extra="ignore")

    url: str
    raw_content: str = Field(validation_alias="raw_content")


class _TavilyFailedResult(BaseModel):
    """Failed Tavily extraction result."""

    model_config = ConfigDict(extra="ignore")

    url: str
    error: str


class _TavilyExtractResponse(BaseModel):
    """Typed subset of Tavily Extract responses."""

    model_config = ConfigDict(extra="ignore")

    results: list[_TavilyResult] = Field(default_factory=list)
    failed_results: list[_TavilyFailedResult] = Field(default_factory=list)


class TavilyFetchProvider(FetchProvider):
    """Fetch markdown using Tavily Extract API."""

    name = "tavily"
    description = "Extract page content using Tavily Extract API."
    base_url = "https://api.tavily.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Tavily and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/extract",
                model=_TavilyExtractResponse,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "urls": [url],
                    "extract_depth": "basic",
                    "format": "markdown",
                },
                timeout_s=self.timeout_s,
            )
            if data.failed_results and not data.results:
                failed_error = data.failed_results[0].error
                if is_not_found_error_message(failed_error, url):
                    raise ProviderError(
                        ErrorType.NOT_FOUND,
                        f"Tavily extraction failed: {failed_error}",
                        self.name,
                    )
                raise ValueError(f"Tavily extraction failed: {failed_error}")

            result = data.results[0] if data.results else None
            if result is None:
                raise ValueError("No content returned from Tavily extract")
            if not result.raw_content:
                raise ValueError("No content returned from Tavily extract")
            return FetchResult(
                url=result.url,
                title=extract_markdown_title(result.raw_content),
                content=result.raw_content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
