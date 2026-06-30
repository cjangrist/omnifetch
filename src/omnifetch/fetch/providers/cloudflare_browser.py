"""Cloudflare Browser Rendering fetch provider: rendered URL to markdown."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_ACCOUNT_ID_ENV_NAME = "CLOUDFLARE_ACCOUNT_ID"
_EMAIL_ENV_NAME = "CLOUDFLARE_EMAIL"
_API_KEY_ENV_NAME = "CLOUDFLARE_API_KEY"
_TIMEOUT_MS = 45_000
_REJECT_RESOURCE_TYPES = ("image", "media", "font")
_ACCOUNT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class _CloudflareBrowserError(BaseModel):
    """Cloudflare Browser Rendering error item."""

    model_config = ConfigDict(extra="ignore")

    code: int | None = None
    message: str = ""


class _CloudflareBrowserResponse(BaseModel):
    """Typed subset of the Cloudflare Browser Rendering markdown response."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    result: str | None = None
    errors: list[_CloudflareBrowserError] = Field(default_factory=list)


def _format_error_message(errors: list[_CloudflareBrowserError]) -> str:
    """Return Cloudflare error messages or the old no-content fallback."""
    messages = [error.message for error in errors if error.message]
    return "; ".join(messages) if messages else "No content returned"


def _validate_account_id(account_id: str | None, provider: str) -> str:
    """Return a trimmed Cloudflare account ID or raise a typed error."""
    if not account_id:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"Cloudflare account ID not found for {provider}",
            provider,
        )
    trimmed_account_id = account_id.strip()
    if _ACCOUNT_ID_PATTERN.fullmatch(trimmed_account_id) is None:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"Invalid Cloudflare account ID for {provider}",
            provider,
        )
    return trimmed_account_id


class CloudflareBrowserFetchProvider(FetchProvider):
    """Fetch rendered markdown using Cloudflare Browser Rendering."""

    name = "cloudflare_browser"
    description = (
        "Fetch URL content using Cloudflare Browser Rendering. Renders "
        "JavaScript before extraction for SPAs and dynamic pages."
    )
    base_url = "https://api.cloudflare.com/client/v4"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (
        _ACCOUNT_ID_ENV_NAME,
        _EMAIL_ENV_NAME,
        _API_KEY_ENV_NAME,
    )

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Cloudflare and return rendered markdown."""
        account_id = _validate_account_id(
            self._secrets.get(_ACCOUNT_ID_ENV_NAME),
            self.name,
        )
        email = validate_api_key(
            self._secrets.get(_EMAIL_ENV_NAME),
            self.name,
        )
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                (
                    f"{self.base_url}/accounts/{account_id}"
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
                message = _format_error_message(data.errors)
                raise ValueError(
                    "Cloudflare Browser Rendering failed: " + message
                )

            return FetchResult(
                url=url,
                title=extract_markdown_title(data.result),
                content=data.result,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
