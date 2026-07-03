"""Cloudflare Browser Rendering fetch provider: rendered URL to markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_ACCOUNT_ID_ENV_NAME = "CLOUDFLARE_ACCOUNT_ID"
_EMAIL_ENV_NAME = "CLOUDFLARE_EMAIL"
_API_KEY_ENV_NAME = "CLOUDFLARE_API_KEY"
_TIMEOUT_MS = 45_000
_REJECT_RESOURCE_TYPES = ("image", "media", "font")


class _CloudflareBrowserError(BaseModel):
    """Typed subset of Cloudflare Browser Rendering errors."""

    model_config = ConfigDict(extra="ignore")

    code: int | None = None
    message: str = ""


class _CloudflareBrowserResponse(BaseModel):
    """Typed subset of Cloudflare Browser Rendering markdown responses."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    result: str | None = None
    errors: tuple[_CloudflareBrowserError, ...] = ()


class CloudflareBrowserFetchProvider(FetchProvider):
    """Fetch rendered markdown using Cloudflare Browser Rendering."""

    name = "cloudflare_browser"
    description = (
        "Fetch URL content using Cloudflare Browser Rendering. Renders "
        "JavaScript before markdown extraction."
    )
    base_url = "https://api.cloudflare.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (
        _ACCOUNT_ID_ENV_NAME,
        _EMAIL_ENV_NAME,
        _API_KEY_ENV_NAME,
    )

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Cloudflare Browser Rendering."""
        account_id = validate_api_key(
            self._secrets.get(_ACCOUNT_ID_ENV_NAME), self.name
        )
        email = validate_api_key(self._secrets.get(_EMAIL_ENV_NAME), self.name)
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME), self.name
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                (
                    f"{self.base_url}/client/v4/accounts/{account_id}"
                    "/browser-rendering/markdown"
                ),
                model=_CloudflareBrowserResponse,
                method="POST",
                headers={
                    "X-Auth-Email": email,
                    "X-Auth-Key": api_key,
                },
                json={
                    "url": url,
                    "rejectResourceTypes": list(_REJECT_RESOURCE_TYPES),
                },
                timeout_s=self.timeout_s,
            )
            if not data.success or not data.result:
                message = "; ".join(
                    error.message for error in data.errors if error.message
                )
                raise ValueError(
                    "Cloudflare Browser Rendering failed: "
                    f"{message or 'No content returned'}"
                )
            return FetchResult(
                url=url,
                title=extract_markdown_title(data.result),
                content=data.result,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
