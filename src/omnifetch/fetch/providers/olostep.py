"""Olostep fetch provider: URL to markdown with JS rendering."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "OLOSTEP_API_KEY"
_TIMEOUT_MS = 30_000


class _OlostepResult(BaseModel):
    """Nested Olostep scrape result."""

    model_config = ConfigDict(extra="ignore")

    markdown_content: str | None = None


class _OlostepScrapeResponse(BaseModel):
    """Typed subset of Olostep scrape responses."""

    model_config = ConfigDict(extra="ignore")

    result: _OlostepResult | None = None


class OlostepFetchProvider(FetchProvider):
    """Fetch markdown using Olostep scrapes API."""

    name = "olostep"
    description = (
        "Fetch URL content using Olostep. Returns markdown with JS rendering "
        "and residential proxies by default."
    )
    base_url = "https://api.olostep.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Olostep and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v1/scrapes",
                model=_OlostepScrapeResponse,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"url": url, "formats": ["markdown"]},
                timeout_s=self.timeout_s,
            )
            content = (
                data.result.markdown_content
                if data.result is not None
                else None
            )
            if not content:
                raise ValueError("Olostep returned empty markdown content")

            return FetchResult(
                url=url,
                title=extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
