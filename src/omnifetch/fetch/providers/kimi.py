"""Kimi fetch provider: URL to markdown through proxied coding API."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.providers.kimi_proxy import (
    build_kimi_fetch_headers,
    proxy_post_via_scrapfly,
    ScrapflyPostRequest,
)
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_KIMI_API_KEY_ENV_NAME = "KIMI_API_KEY"
_SCRAPFLY_API_KEY_ENV_NAME = "SCRAPFLY_API_KEY"
_FETCH_PATH = "/coding/v1/fetch"
_TIMEOUT_MS = 60_000
_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300


class _KimiFetchResponse(BaseModel):
    """Typed subset of Kimi coding-API fetch responses."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    markdown: str | None = None
    title: str | None = None


class KimiFetchProvider(FetchProvider):
    """Fetch clean markdown using Kimi coding API through Scrapfly."""

    name = "kimi"
    description = (
        "Fetch URL content via Kimi (Moonshot AI) coding API. "
        "Returns clean markdown."
    )
    base_url = "https://api.kimi.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (
        _KIMI_API_KEY_ENV_NAME,
        _SCRAPFLY_API_KEY_ENV_NAME,
    )

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Kimi and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_KIMI_API_KEY_ENV_NAME),
            self.name,
        )
        scrapfly_api_key = validate_api_key(
            self._secrets.get(_SCRAPFLY_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            target_body = json.dumps({"url": url}, separators=(",", ":"))
            proxied = await proxy_post_via_scrapfly(
                self._client,
                ScrapflyPostRequest(
                    self.name,
                    f"{self.base_url}{_FETCH_PATH}",
                    build_kimi_fetch_headers(api_key),
                    target_body,
                    scrapfly_api_key,
                    self.timeout_ms,
                ),
            )
            if not _HTTP_OK_MIN <= proxied.status < _HTTP_OK_MAX:
                raise ProviderError(
                    ErrorType.PROVIDER_ERROR,
                    f"Kimi fetch HTTP {proxied.status}: {proxied.body[:200]}",
                    self.name,
                )

            data = _KimiFetchResponse.model_validate_json(proxied.body)
            content = (data.markdown or "").strip()
            if not content:
                raise ValueError("Kimi fetch returned empty markdown")
            return FetchResult(
                url=data.url or url,
                title=(data.title or "").strip()
                or extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
