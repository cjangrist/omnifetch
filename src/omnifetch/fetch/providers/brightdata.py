"""Bright Data Web Unlocker fetch provider: URL to native markdown."""

from __future__ import annotations

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_text
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "BRIGHT_DATA_API_KEY"
_ZONE_ENV_NAME = "BRIGHT_DATA_ZONE"
_DEFAULT_ZONE = "unblocker"
_TIMEOUT_MS = 30_000


def _resolve_zone(zone: str | None) -> str:
    """Return the configured Bright Data zone or the source default."""
    normalized_zone = zone.strip() if zone else ""
    return normalized_zone or _DEFAULT_ZONE


class BrightDataFetchProvider(FetchProvider):
    """Fetch markdown using Bright Data Web Unlocker."""

    name = "brightdata"
    description = (
        "Fetch URL content using Bright Data Web Unlocker. Returns native "
        "markdown with anti-bot bypass."
    )
    base_url = "https://api.brightdata.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Bright Data and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        zone = _resolve_zone(self._secrets.get(_ZONE_ENV_NAME))

        try:
            content = await http_text(
                self._client,
                self.name,
                f"{self.base_url}/request",
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "zone": zone,
                    "url": url,
                    "format": "raw",
                    "data_format": "markdown",
                },
                timeout_s=self.timeout_s,
            )
            if not content:
                raise ValueError("Bright Data returned empty markdown")

            return FetchResult(
                url=url,
                title=extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
