"""Scrapeless fetch provider: Web Unlocker URL to markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "SCRAPELESS_API_KEY"
_TIMEOUT_MS = 30_000
_SUCCESS_CODE = 200


class _ScrapelessResponse(BaseModel):
    """Typed subset of Scrapeless Web Unlocker responses."""

    model_config = ConfigDict(extra="ignore")

    code: int
    data: str | None = None


class ScrapelessFetchProvider(FetchProvider):
    """Fetch markdown using Scrapeless Web Unlocker with JS rendering."""

    name = "scrapeless"
    description = (
        "Fetch URL content using Scrapeless Web Unlocker. Returns markdown "
        "with JS rendering."
    )
    base_url = "https://api.scrapeless.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Scrapeless and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/api/v2/unlocker/request",
                model=_ScrapelessResponse,
                method="POST",
                headers={"x-api-token": api_key},
                json={
                    "actor": "unlocker.webunlocker",
                    "input": {
                        "url": url,
                        "method": "GET",
                        "redirect": False,
                        "jsRender": {
                            "enabled": True,
                            "response": {"type": "markdown"},
                        },
                    },
                    "proxy": {"country": "ANY"},
                },
                timeout_s=self.timeout_s,
            )
            if data.code != _SUCCESS_CODE:
                raise ValueError(f"Scrapeless returned code {data.code}")
            if not data.data:
                raise ValueError("Scrapeless returned empty data")
            return FetchResult(
                url=url,
                title=extract_markdown_title(data.data),
                content=data.data,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
