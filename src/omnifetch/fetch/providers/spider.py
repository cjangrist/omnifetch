"""Spider.cloud fetch provider: URL to markdown through smart scraping."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, RootModel

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_TOKEN_ENV_NAME = "SPIDER_CLOUD_API_TOKEN"
_TIMEOUT_MS = 30_000


class _SpiderPage(BaseModel):
    """One page object returned by Spider.cloud scrape responses."""

    model_config = ConfigDict(extra="ignore")

    url: str = ""
    status: int | None = None
    content: str = ""
    error: str | None = None


class _SpiderScrapeResponse(RootModel[list[_SpiderPage]]):
    """Typed top-level Spider.cloud scrape response array."""


def _first_page(data: _SpiderScrapeResponse) -> _SpiderPage:
    """Return the first scraped page or raise for an empty response."""
    if not data.root:
        raise ValueError("Spider returned empty response")
    return data.root[0]


def _validate_page(page: _SpiderPage) -> None:
    """Raise for Spider page-level errors or unusable markdown content."""
    if page.error:
        raise ValueError(f"Spider scrape error: {page.error}")
    if not page.content:
        raise ValueError("Spider returned empty content")


class SpiderFetchProvider(FetchProvider):
    """Fetch page markdown using Spider.cloud scrape API."""

    name = "spider"
    description = (
        "Fetch URL content using Spider.cloud. Returns markdown via smart "
        "request mode."
    )
    base_url = "https://api.spider.cloud"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_TOKEN_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Spider.cloud and return normalized markdown."""
        api_token = validate_api_key(
            self._secrets.get(_API_TOKEN_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/scrape",
                model=_SpiderScrapeResponse,
                method="POST",
                headers={"Authorization": f"Bearer {api_token}"},
                json={
                    "url": url,
                    "return_format": "markdown",
                },
                timeout_s=self.timeout_s,
            )
            page = _first_page(data)
            _validate_page(page)
            return FetchResult(
                url=url,
                title=extract_markdown_title(page.content),
                content=page.content,
                source_provider=self.name,
                metadata={"status": page.status},
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
