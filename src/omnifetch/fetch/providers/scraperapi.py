"""ScraperAPI fetch provider: URL to native markdown output."""

from __future__ import annotations

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_text
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "SCRAPERAPI_API_KEY"
_TIMEOUT_MS = 30_000


class ScraperAPIFetchProvider(FetchProvider):
    """Fetch markdown using ScraperAPI."""

    name = "scraperapi"
    description = (
        "Fetch URL content using ScraperAPI. Returns native markdown output."
    )
    base_url = "https://api.scraperapi.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through ScraperAPI and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            content = await http_text(
                self._client,
                self.name,
                self.base_url,
                params={
                    "api_key": api_key,
                    "url": url,
                    "output_format": "markdown",
                },
                timeout_s=self.timeout_s,
            )
            if not content:
                raise ValueError("ScraperAPI returned empty content")
            return FetchResult(
                url=url,
                title=extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
