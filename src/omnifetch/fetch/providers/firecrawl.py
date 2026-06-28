"""Firecrawl fetch provider: URL to clean markdown plus metadata."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "FIRECRAWL_API_KEY"
_TIMEOUT_MS = 30_000


class _FirecrawlMetadata(BaseModel):
    """Metadata returned by Firecrawl v2 scrape responses."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    source_url: str | None = Field(default=None, validation_alias="sourceURL")
    status_code: int | None = Field(
        default=None,
        validation_alias="statusCode",
    )


class _FirecrawlData(BaseModel):
    """Nested Firecrawl content payload."""

    model_config = ConfigDict(extra="ignore")

    markdown: str | None = None
    metadata: _FirecrawlMetadata | None = None


class _FirecrawlScrapeResponse(BaseModel):
    """Typed subset of the Firecrawl scrape response."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    data: _FirecrawlData | None = None


class FirecrawlFetchProvider(FetchProvider):
    """Fetch clean markdown using Firecrawl v2 scrape."""

    name = "firecrawl"
    description = "Scrape a single URL using Firecrawl v2 API."
    base_url = "https://api.firecrawl.dev"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through Firecrawl and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v2/scrape",
                model=_FirecrawlScrapeResponse,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
                timeout_s=self.timeout_s,
            )
            if not data.success:
                raise ValueError("Firecrawl scrape failed")
            if data.data is None:
                raise ValueError("Firecrawl scrape returned no content")
            if not data.data.markdown:
                raise ValueError("Firecrawl scrape returned no content")

            metadata = data.data.metadata
            return FetchResult(
                url=metadata.source_url
                if metadata and metadata.source_url
                else url,
                title=metadata.title if metadata and metadata.title else "",
                content=data.data.markdown,
                source_provider=self.name,
                metadata=None
                if metadata is None
                else {
                    "description": metadata.description,
                    "status_code": metadata.status_code,
                },
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
