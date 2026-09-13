"""fastCRW fetch provider: URL to clean markdown plus metadata.

The scrape surface is Firecrawl-compatible in request shape but diverges in its
error envelope. A missing target answers HTTP 200 with ``success: false`` and a
populated ``data.markdown`` holding some *other* page, so no content is read
until the outcome is known.

``metadata.statusCode`` is the authority on whether the target was missing, and
it is read before the success flag, because the accompanying ``error`` string is
not reliably about the target at all: a live 404 came back as
``lightpanda_budget_truncated`` alongside ``statusCode: 404``. Judging that
response by its message alone demotes a definitive miss to a transient failure
and spends another provider on a page that does not exist.
"""

from __future__ import annotations

from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    is_not_found_error_message,
    validate_api_key,
)

_API_KEY_ENV_NAME = "CRW_API_KEY"
_TIMEOUT_MS = 30_000

# Statuses that settle the question: the target is not there and retrying a
# different provider cannot change that. 410 belongs here as firmly as 404 --
# it is a stronger statement, not a weaker one.
_DEFINITELY_MISSING_STATUSES = frozenset({404, 410})


class _FastcrwMetadata(BaseModel):
    """Metadata returned by fastCRW v1 scrape responses."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    source_url: str | None = Field(default=None, validation_alias="sourceURL")
    status_code: int | None = Field(
        default=None,
        validation_alias="statusCode",
    )


class _FastcrwData(BaseModel):
    """Nested fastCRW content payload."""

    model_config = ConfigDict(extra="ignore")

    markdown: str | None = None
    metadata: _FastcrwMetadata | None = None


class _FastcrwScrapeResponse(BaseModel):
    """Typed subset of the fastCRW scrape response."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    data: _FastcrwData | None = None
    error: str | None = None


class FastcrwFetchProvider(FetchProvider):
    """Fetch clean markdown using the fastCRW v1 scrape endpoint."""

    name = "fastcrw"
    description = "Scrape a single URL using the fastCRW v1 API."
    base_url = "https://api.fastcrw.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    def _api_key(self) -> str:
        """Return the credential for the hosted endpoint."""
        return validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through fastCRW and return normalized markdown."""
        api_key = self._api_key()
        try:
            data = await http_json(
                self._client,
                self.name,
                f"{self.base_url}/v1/scrape",
                model=_FastcrwScrapeResponse,
                method="POST",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
                timeout_s=self.timeout_s,
            )
            metadata = data.data.metadata if data.data else None
            if (
                metadata
                and metadata.status_code in _DEFINITELY_MISSING_STATUSES
            ):
                raise ProviderError(
                    ErrorType.NOT_FOUND,
                    f"fastCRW target returned status {metadata.status_code}",
                    self.name,
                )
            if not data.success:
                self._raise_unsuccessful(data.error, url)
            if data.data is None:
                raise ValueError("fastCRW scrape returned no content")
            if not data.data.markdown:
                raise ValueError("fastCRW scrape returned no content")
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

    def _raise_unsuccessful(self, error: str | None, url: str) -> NoReturn:
        """Raise for a scrape the upstream itself reports as failed."""
        if not error:
            raise ValueError("fastCRW scrape failed")
        if is_not_found_error_message(error, url):
            raise ProviderError(
                ErrorType.NOT_FOUND,
                f"fastCRW scrape failed: {error}",
                self.name,
            )
        raise ValueError(f"fastCRW scrape failed: {error}")
