"""You.com fetch provider: URL to markdown through Contents API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, RootModel

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "YOU_API_KEY"
_TIMEOUT_MS = 30_000


class _YouContentsResult(BaseModel):
    """Typed subset of one You.com Contents result."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    title: str | None = None
    markdown: str | None = None


class _YouContentsResponse(RootModel[list[_YouContentsResult]]):
    """You.com Contents response payload."""


class YouFetchProvider(FetchProvider):
    """Fetch markdown using You.com Contents API."""

    name = "you"
    description = (
        "Fetch URL content using You.com Contents API. Returns markdown "
        "with metadata."
    )
    base_url = "https://ydc-index.io"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through You.com and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v1/contents",
                model=_YouContentsResponse,
                method="POST",
                headers={"X-API-Key": api_key},
                json={"urls": [url], "formats": ["markdown"]},
                timeout_s=self.timeout_s,
            )
            result = data.root[0] if data.root else None
            if result is None or not result.markdown:
                raise ValueError("You.com Contents returned no markdown")

            return FetchResult(
                url=result.url or url,
                title=result.title or "",
                content=result.markdown,
                source_provider=self.name,
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
