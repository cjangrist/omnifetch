"""ScrapingBee fetch provider: URL to native markdown output."""

from __future__ import annotations

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_text
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "SCRAPINGBEE_API_KEY"
_TIMEOUT_MS = 30_000


class ScrapingBeeFetchProvider(FetchProvider):
    """Fetch native markdown using ScrapingBee."""

    name = "scrapingbee"
    description = (
        "Fetch URL content using ScrapingBee. Returns native markdown output."
    )
    base_url = "https://app.scrapingbee.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through ScrapingBee and return markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            content = await http_text(
                self._client,
                self.name,
                f"{self.base_url}/api/v1",
                params={
                    "api_key": api_key,
                    "url": url,
                    "render_js": "false",
                    "return_page_markdown": "true",
                },
                timeout_s=self.timeout_s,
            )
            if not content:
                raise ValueError("ScrapingBee returned empty markdown")

            return FetchResult(
                url=url,
                title=extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
