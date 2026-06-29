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


class _ScrapeGraphAIMarkdownifyResponse(BaseModel):
    """Typed subset of ScrapeGraphAI markdownify responses."""

    model_config = ConfigDict(extra="ignore")

    request_id: str = Field(validation_alias="request_id")
    status: str
    website_url: str = Field(validation_alias="website_url")
    result: str | None = None
    error: str = ""


class ScrapeGraphAIFetchProvider(FetchProvider):
    """Fetch markdown using ScrapeGraphAI markdownify endpoint."""

    name = "scrapegraphai"
    description = (
        "Fetch URL content using ScrapeGraphAI markdownify endpoint. "
        "Returns clean markdown."
    )
    base_url = "https://api.scrapegraphai.com"
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
                f"{self.base_url}/v1/markdownify",
                model=_ScrapeGraphAIMarkdownifyResponse,
                method="POST",
                headers={"SGAI-APIKEY": api_key},
                json={"website_url": url},
                timeout_s=self.timeout_s,
            )
            if data.status != "completed" or data.error:
                raise ValueError(
                    "ScrapeGraphAI failed: "
                    f"{data.error or data.status or 'unknown error'}"
                )
            if not data.result:
                raise ValueError("ScrapeGraphAI returned empty result")

            return FetchResult(
                url=url,
                title=extract_markdown_title(data.result),
                content=data.result,
                source_provider=self.name,
                metadata={"request_id": data.request_id},
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
