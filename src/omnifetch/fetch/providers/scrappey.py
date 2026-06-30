"""Scrappey fetch provider: rendered page innerText plus HTML title."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_html_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "SCRAPPEY_API_KEY"
_TIMEOUT_MS = 30_000


class _ScrappeySolution(BaseModel):
    """Typed subset of the Scrappey browser solution payload."""

    model_config = ConfigDict(extra="ignore")

    inner_text: str | None = Field(default=None, validation_alias="innerText")
    response: str | None = None
    current_url: str | None = Field(default=None, validation_alias="currentUrl")
    status_code: int | None = Field(default=None, validation_alias="statusCode")


class _ScrappeyResponse(BaseModel):
    """Typed subset of the Scrappey API response."""

    model_config = ConfigDict(extra="ignore")

    data: str | None = None
    solution: _ScrappeySolution | None = None


class ScrappeyFetchProvider(FetchProvider):
    """Fetch visible page text using the Scrappey headless browser API."""

    name = "scrappey"
    description = (
        "Fetch URL content using Scrappey headless browser API. Returns "
        "extracted page text."
    )
    base_url = "https://publisher.scrappey.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Scrappey and return visible page text."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/api/v1",
                model=_ScrappeyResponse,
                method="POST",
                params={"key": api_key},
                json={"cmd": "request.get", "url": url},
                timeout_s=self.timeout_s,
            )
            if data.data != "success" or data.solution is None:
                status = (
                    data.data
                    if data.data is not None
                    else "(missing data field)"
                )
                raise ValueError(f"Scrappey request failed: {status}")

            content = data.solution.inner_text
            if not content:
                raise ValueError("Scrappey returned empty innerText")

            title = (
                extract_html_title(data.solution.response)
                if data.solution.response
                else ""
            )
            return FetchResult(
                url=url,
                title=title,
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
