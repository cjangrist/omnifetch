"""OpenGraph.io fetch provider: URL to structured extracted text."""

from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import (
    handle_provider_error,
    validate_api_key,
)

_API_KEY_ENV_NAME = "OPENGRAPH_IO_API_KEY"
_TIMEOUT_MS = 30_000
_TITLE_TAG_NAMES = frozenset({"title", "h1"})


class _OpenGraphTag(BaseModel):
    """Tag extracted by OpenGraph.io Extract API."""

    model_config = ConfigDict(extra="ignore")

    tag: str = ""
    inner_text: str = Field(default="", validation_alias="innerText")
    position: int | None = None


class _OpenGraphRequestInfo(BaseModel):
    """Request metadata returned by OpenGraph.io Extract API."""

    model_config = ConfigDict(extra="ignore")

    host: str | None = None
    response_code: int | None = Field(
        default=None,
        validation_alias="responseCode",
    )


class _OpenGraphExtractResponse(BaseModel):
    """Typed subset of the OpenGraph.io Extract API response."""

    model_config = ConfigDict(extra="ignore")

    tags: list[_OpenGraphTag] = Field(default_factory=list)
    concatenated_text: str = Field(
        default="",
        validation_alias="concatenatedText",
    )
    request_info: _OpenGraphRequestInfo | None = Field(
        default=None,
        validation_alias="requestInfo",
    )


def _build_extract_url(base_url: str, target_url: str) -> str:
    """Return the OpenGraph.io extract endpoint for ``target_url``."""
    encoded_target_url = quote(target_url, safe="")
    return f"{base_url}/api/1.1/extract/{encoded_target_url}"


def _build_content(data: _OpenGraphExtractResponse) -> str:
    """Return extracted text using OpenGraph.io content precedence."""
    if data.concatenated_text:
        return data.concatenated_text
    return "\n\n".join(tag.inner_text for tag in data.tags)


def _extract_title(tags: list[_OpenGraphTag]) -> str:
    """Return the first title-like tag text."""
    return next(
        (tag.inner_text for tag in tags if tag.tag in _TITLE_TAG_NAMES),
        "",
    )


class OpenGraphFetchProvider(FetchProvider):
    """Fetch structured text extraction using OpenGraph.io."""

    name = "opengraph"
    description = (
        "Fetch URL content using OpenGraph.io Extract API. Returns structured "
        "text extraction."
    )
    base_url = "https://opengraph.io"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through OpenGraph.io and return extracted text."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        try:
            data = await http_json(
                self._client,
                self.name,
                _build_extract_url(self.base_url, url),
                model=_OpenGraphExtractResponse,
                params={"app_id": api_key},
                timeout_s=self.timeout_s,
            )
            if not data.concatenated_text and not data.tags:
                raise ValueError("OpenGraph.io returned empty extraction")

            content = _build_content(data)
            if not content:
                raise ValueError("OpenGraph.io returned empty content")

            request_info = data.request_info
            return FetchResult(
                url=url,
                title=_extract_title(data.tags),
                content=content,
                source_provider=self.name,
                metadata={
                    "response_code": request_info.response_code
                    if request_info
                    else None,
                    "tag_count": len(data.tags),
                },
            )
        except Exception as error:
            handle_provider_error(error, self.name, "fetch URL content")
