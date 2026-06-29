"""Decodo fetch provider: URL to markdown through Web Scraping API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "DECODO_WEB_SCRAPING_API_KEY"
_TIMEOUT_MS = 60_000


class _DecodoResult(BaseModel):
    """Single Decodo scrape result."""

    model_config = ConfigDict(extra="ignore")

    content: str = ""
    status_code: int | None = None
    task_id: str | None = None


class _DecodoScrapeResponse(BaseModel):
    """Typed subset of Decodo scrape responses."""

    model_config = ConfigDict(extra="ignore")

    results: list[_DecodoResult] = Field(default_factory=list)


class DecodoFetchProvider(FetchProvider):
    """Fetch markdown using Decodo Web Scraping API."""

    name = "decodo"
    description = (
        "Fetch URL content using Decodo Web Scraping API. Returns markdown "
        "output."
    )
    base_url = "https://scraper-api.decodo.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Decodo and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v2/scrape",
                model=_DecodoScrapeResponse,
                method="POST",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Basic {api_key}",
                },
                json={"url": url, "markdown": True},
                timeout_s=self.timeout_s,
            )
            result = data.results[0] if data.results else None
            if result is None or not result.content:
                raise ValueError("Decodo returned empty content")

            return FetchResult(
                url=url,
                title=extract_markdown_title(result.content),
                content=result.content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
