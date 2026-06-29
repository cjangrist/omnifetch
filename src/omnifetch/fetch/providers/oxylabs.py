"""Oxylabs fetch provider: URL to markdown through Web Scraper API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    basic_auth,
    handle_provider_error,
    validate_api_key,
)

_USERNAME_ENV_NAME = "OXYLABS_WEB_SCRAPER_USERNAME"
_PASSWORD_ENV_NAME = "OXYLABS_WEB_SCRAPER_PASSWORD"
_TIMEOUT_MS = 30_000


class _OxylabsResult(BaseModel):
    """Single Oxylabs realtime query result."""

    model_config = ConfigDict(extra="ignore")

    content: str | None = None
    status_code: int | None = None


class _OxylabsResponse(BaseModel):
    """Typed subset of Oxylabs realtime query responses."""

    model_config = ConfigDict(extra="ignore")

    results: list[_OxylabsResult] = Field(default_factory=list)


class OxylabsFetchProvider(FetchProvider):
    """Fetch markdown using Oxylabs Web Scraper API realtime endpoint."""

    name = "oxylabs"
    description = (
        "Fetch URL content using Oxylabs Web Scraper API. Returns markdown "
        "via realtime endpoint."
    )
    base_url = "https://realtime.oxylabs.io"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_USERNAME_ENV_NAME, _PASSWORD_ENV_NAME)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Oxylabs and return normalized markdown."""
        username = validate_api_key(
            self._secrets.get(_USERNAME_ENV_NAME),
            self.name,
        )
        password = validate_api_key(
            self._secrets.get(_PASSWORD_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v1/queries",
                model=_OxylabsResponse,
                method="POST",
                headers={
                    "Authorization": f"Basic {basic_auth(username, password)}"
                },
                json={
                    "source": "universal",
                    "url": url,
                    "markdown": True,
                },
                timeout_s=self.timeout_s,
            )
            result = data.results[0] if data.results else None
            if result is None or not result.content:
                raise ValueError("Oxylabs returned empty content")
            return FetchResult(
                url=url,
                title=extract_markdown_title(result.content),
                content=result.content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
