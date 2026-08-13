"""ScrapeGraphAI fetch provider: URL to clean markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "SCRAPEGRAPHAI_API_KEY"
_TIMEOUT_MS = 30_000


class _ScrapeGraphAIMarkdownResult(BaseModel):
    """Typed markdown result returned by ScrapeGraphAI Scrape."""

    model_config = ConfigDict(extra="ignore")

    data: list[str] | None = None


class _ScrapeGraphAIResults(BaseModel):
    """Typed format results returned by ScrapeGraphAI Scrape."""

    model_config = ConfigDict(extra="ignore")

    markdown: _ScrapeGraphAIMarkdownResult | None = None


class _ScrapeGraphAIMetadata(BaseModel):
    """Typed metadata returned by ScrapeGraphAI Scrape."""

    model_config = ConfigDict(extra="ignore")

    content_type: str | None = Field(default=None, alias="contentType")


class _ScrapeGraphAIScrapeResponse(BaseModel):
    """Typed subset of ScrapeGraphAI v2 Scrape responses."""

    model_config = ConfigDict(extra="ignore")

    id: str
    results: _ScrapeGraphAIResults | None = None
    metadata: _ScrapeGraphAIMetadata | None = None


class ScrapeGraphAIFetchProvider(FetchProvider):
    """Fetch markdown using the ScrapeGraphAI v2 Scrape endpoint."""

    name = "scrapegraphai"
    description = (
        "Fetch URL content using ScrapeGraphAI v2 Scrape. Returns clean "
        "markdown."
    )
    base_url = "https://v2-api.scrapegraphai.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through ScrapeGraphAI and return markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/api/scrape",
                model=_ScrapeGraphAIScrapeResponse,
                method="POST",
                headers={"SGAI-APIKEY": api_key},
                json={"url": url, "formats": [{"type": "markdown"}]},
                timeout_s=self.timeout_s,
            )
            markdown_values = (
                data.results.markdown.data
                if data.results is not None
                and data.results.markdown is not None
                and data.results.markdown.data is not None
                else []
            )
            markdown = next(
                (value for value in markdown_values if value.strip()), None
            )
            if markdown is None:
                raise ValueError("ScrapeGraphAI returned empty markdown data")

            metadata = {"request_id": data.id}
            if data.metadata is not None and data.metadata.content_type:
                metadata["content_type"] = data.metadata.content_type

            return FetchResult(
                url=url,
                title=extract_markdown_title(markdown),
                content=markdown,
                source_provider=self.name,
                metadata=metadata,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
