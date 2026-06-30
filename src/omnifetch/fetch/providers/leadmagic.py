"""LeadMagic Web2MD fetch provider: URL to boilerplate-free markdown."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.html import extract_markdown_title
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "LEADMAGIC_API_KEY"
_TIMEOUT_MS = 30_000


class _LeadMagicResponse(BaseModel):
    """Typed subset of LeadMagic Web2MD responses."""

    model_config = ConfigDict(extra="ignore")

    markdown: str | None = None
    title: str | None = None
    url: str | None = None


class LeadMagicFetchProvider(FetchProvider):
    """Fetch URL content using LeadMagic Web2MD API."""

    name = "leadmagic"
    description = (
        "Fetch URL content using LeadMagic Web2MD API. Returns clean "
        "markdown with boilerplate removed."
    )
    base_url = "https://api.web2md.app"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through LeadMagic and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            response = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/api/scrape",
                model=_LeadMagicResponse,
                method="POST",
                headers={"X-API-Key": api_key},
                json={"url": url},
                timeout_s=self.timeout_s,
            )
            content = response.markdown
            if not content:
                raise ValueError("LeadMagic returned empty markdown")

            return FetchResult(
                url=url,
                title=response.title or extract_markdown_title(content),
                content=content,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
