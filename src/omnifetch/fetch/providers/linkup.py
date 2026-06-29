"""Linkup fetch provider: URL to clean markdown."""

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

_API_KEY_ENV_NAME = "LINKUP_API_KEY"
_TIMEOUT_MS = 30_000


class _LinkupFetchResponse(BaseModel):
    """Typed subset of Linkup Content Fetch responses."""

    model_config = ConfigDict(extra="ignore")

    markdown: str = ""


class LinkupFetchProvider(FetchProvider):
    """Fetch clean markdown using Linkup Content Fetch API."""

    name = "linkup"
    description = (
        "Fetch URL content using Linkup Content Fetch API. Returns clean "
        "markdown."
    )
    base_url = "https://api.linkup.so"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Linkup and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v1/fetch",
                model=_LinkupFetchResponse,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"url": url},
                timeout_s=self.timeout_s,
            )
            if not data.markdown:
                raise ValueError("Linkup returned no markdown content")
            return FetchResult(
                url=url,
                title=extract_markdown_title(data.markdown),
                content=data.markdown,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
