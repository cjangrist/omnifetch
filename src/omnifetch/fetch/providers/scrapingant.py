"""ScrapingAnt fetch provider: URL to LLM-ready markdown."""

from __future__ import annotations

from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "SCRAPINGANT_API_KEY"
_API_KEY_QUERY_PARAM = "x-api-key"
_TIMEOUT_MS = 30_000


class _ScrapingAntMarkdownResponse(BaseModel):
    """Typed subset of ScrapingAnt markdown responses."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    markdown: str = ""


class ScrapingAntFetchProvider(FetchProvider):
    """Fetch markdown using ScrapingAnt's LLM-ready endpoint."""

    name = "scrapingant"
    description = (
        "Extract page content as markdown using ScrapingAnt LLM-ready endpoint."
    )
    base_url = "https://api.scrapingant.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through ScrapingAnt and return normalized markdown."""
        credential_value = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            query = urlencode(
                {"url": url, _API_KEY_QUERY_PARAM: credential_value}
            )
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v2/markdown?{query}",
                model=_ScrapingAntMarkdownResponse,
                timeout_s=self.timeout_s,
            )
            if not data.markdown:
                raise ValueError("ScrapingAnt returned no markdown content")

            return FetchResult(
                url=data.url or url,
                title=extract_markdown_title(data.markdown),
                content=data.markdown,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
