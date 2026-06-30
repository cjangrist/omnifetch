"""Scrape.do fetch provider: proxied URL to markdown."""

from __future__ import annotations

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_text
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_TOKEN_ENV_NAME = "SCRAPE_DO_API_TOKEN"
_TIMEOUT_MS = 30_000


class ScrapeDoFetchProvider(FetchProvider):
    """Fetch markdown content using Scrape.do."""

    name = "scrapedo"
    description = (
        "Fetch URL content using Scrape.do. Returns markdown via "
        "proxy-based scraping."
    )
    base_url = "https://api.scrape.do"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_TOKEN_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Scrape.do and return normalized markdown."""
        api_token = validate_api_key(
            self._secrets.get(_API_TOKEN_ENV_NAME),
            self.name,
        )
        try:
            content = await http_text(
                self._client,
                self.name,
                self.base_url,
                params={"token": api_token, "url": url, "output": "markdown"},
                timeout_s=self.timeout_s,
            )
            if not content:
                raise ValueError("Scrape.do returned empty content")
            return FetchResult(
                url=url,
                title=extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
