"""Scrapfly fetch provider: anti-bot URL scrape to markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "SCRAPFLY_API_KEY"
_TIMEOUT_MS = 30_000


class _ScrapflyResult(BaseModel):
    """Nested Scrapfly scrape result payload."""

    model_config = ConfigDict(extra="ignore")

    content: str | None = None
    status_code: int | None = None
    url: str | None = None
    format: str | None = None


class _ScrapflyScrapeResponse(BaseModel):
    """Typed subset of Scrapfly scrape responses."""

    model_config = ConfigDict(extra="ignore")

    result: _ScrapflyResult | None = None


class ScrapflyFetchProvider(FetchProvider):
    """Fetch markdown using Scrapfly scrape API."""

    name = "scrapfly"
    description = (
        "Fetch URL content using Scrapfly.io. Returns markdown with anti-bot "
        "bypass."
    )
    base_url = "https://api.scrapfly.io"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Scrapfly and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/scrape",
                model=_ScrapflyScrapeResponse,
                method="GET",
                params={"key": api_key, "url": url, "format": "markdown"},
                timeout_s=self.timeout_s,
            )
            if data.result is None or not data.result.content:
                raise ValueError("Scrapfly returned empty content")

            return FetchResult(
                url=url,
                title=extract_markdown_title(data.result.content),
                content=data.result.content,
                source_provider=self.name,
                metadata={"status_code": data.result.status_code},
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
