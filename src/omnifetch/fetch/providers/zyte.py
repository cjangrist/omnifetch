"""Zyte fetch provider: URL to structured page content."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    basic_auth,
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "ZYTE_API_KEY"
_TIMEOUT_MS = 30_000


class _ZytePageContent(BaseModel):
    """Typed subset of Zyte automatic page-content extraction."""

    model_config = ConfigDict(extra="ignore")

    headline: str | None = None
    title: str | None = None
    item_main: str | None = Field(default=None, validation_alias="itemMain")
    canonical_url: str | None = Field(
        default=None,
        validation_alias="canonicalUrl",
    )
    metadata: dict[str, Any] | None = None


class _ZyteExtractResponse(BaseModel):
    """Typed subset of Zyte extract responses."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    status_code: int | None = Field(
        default=None,
        validation_alias="statusCode",
    )
    page_content: _ZytePageContent | None = Field(
        default=None,
        validation_alias="pageContent",
    )


def _build_metadata(page_content: _ZytePageContent) -> dict[str, Any] | None:
    """Return Zyte metadata fields that were present upstream."""
    metadata: dict[str, Any] = {}
    if page_content.headline:
        metadata["headline"] = page_content.headline
    if page_content.metadata is not None:
        metadata["zyte_metadata"] = page_content.metadata
    return metadata or None


class ZyteFetchProvider(FetchProvider):
    """Fetch clean page content using Zyte automatic extraction."""

    name = "zyte"
    description = (
        "Extract clean page content using Zyte API automatic extraction. "
        "Returns structured text with headline, title, and main content."
    )
    base_url = "https://api.zyte.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Zyte and return normalized content."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v1/extract",
                model=_ZyteExtractResponse,
                method="POST",
                headers={"Authorization": f"Basic {basic_auth(api_key)}"},
                json={"url": url, "pageContent": True},
                timeout_s=self.timeout_s,
            )
            page_content = data.page_content
            if page_content is None or not page_content.item_main:
                raise ValueError("Zyte returned no page content")

            return FetchResult(
                url=page_content.canonical_url or data.url or url,
                title=page_content.title or page_content.headline or "",
                content=page_content.item_main,
                source_provider=self.name,
                metadata=_build_metadata(page_content),
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
