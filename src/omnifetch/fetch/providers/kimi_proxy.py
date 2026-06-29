"""Kimi CLI headers and Scrapfly proxy support for the fetch provider."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, ProviderError

_KIMI_PLATFORM = "kimi_cli"
_KIMI_CLI_VERSION = "1.37.0"
_KIMI_DEVICE_NAME = "device-01"
_KIMI_DEVICE_MODEL = "Linux 6.17.0-1009-gcp x86_64"
_KIMI_OS_VERSION = "#9-Ubuntu SMP Fri Mar  6 21:21:14 UTC 2026"
_KIMI_DEVICE_ID = "babf43cbff8d4c789b8a8fabc85b0490"

_SCRAPFLY_BASE_URL = "https://api.scrapfly.io"
_SCRAPFLY_SCRAPE_PATH = "/scrape"
_SCRAPFLY_URL = f"{_SCRAPFLY_BASE_URL}{_SCRAPFLY_SCRAPE_PATH}"
_SCRAPFLY_COUNTRY = "us"


@dataclass(frozen=True, slots=True)
class ProxiedResponse:
    """Upstream response forwarded by Scrapfly."""

    status: int
    body: str
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class ScrapflyPostRequest:
    """Request data needed to forward one POST through Scrapfly."""

    provider_name: str
    target_url: str
    target_headers: dict[str, str]
    target_body: str
    scrapfly_api_key: str
    timeout_ms: int


class _ScrapflyResult(BaseModel):
    """Typed subset of a Scrapfly scrape result payload."""

    model_config = ConfigDict(extra="ignore")

    status_code: int | None = None
    content: str | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)


class _ScrapflyResponse(BaseModel):
    """Typed subset of a Scrapfly scrape response."""

    model_config = ConfigDict(extra="ignore")

    result: _ScrapflyResult | None = None


def _new_tool_call_id(prefix: str) -> str:
    """Return a Kimi CLI-shaped tool call identifier."""
    return f"{prefix}-{uuid4().hex[:12]}"


def _build_common_msh_headers() -> dict[str, str]:
    """Return stable Kimi CLI device identity headers."""
    return {
        "X-Msh-Platform": _KIMI_PLATFORM,
        "X-Msh-Version": _KIMI_CLI_VERSION,
        "X-Msh-Device-Name": _KIMI_DEVICE_NAME,
        "X-Msh-Device-Model": _KIMI_DEVICE_MODEL,
        "X-Msh-Os-Version": _KIMI_OS_VERSION,
        "X-Msh-Device-Id": _KIMI_DEVICE_ID,
    }


def build_kimi_fetch_headers(api_key: str) -> dict[str, str]:
    """Return Kimi coding-API fetch headers matching Kimi CLI identity."""
    return {
        "User-Agent": f"KimiCLI/{_KIMI_CLI_VERSION}",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Msh-Tool-Call-Id": _new_tool_call_id("fetch"),
        **_build_common_msh_headers(),
    }


def _build_scrapfly_params(
    target_url: str,
    target_headers: dict[str, str],
    scrapfly_api_key: str,
) -> tuple[tuple[str, str], ...]:
    """Return Scrapfly scrape query params with forwarded target headers."""
    return (
        ("key", scrapfly_api_key),
        ("url", target_url),
        ("method", "POST"),
        ("country", _SCRAPFLY_COUNTRY),
        *(
            (f"headers[{name}]", value)
            for name, value in target_headers.items()
        ),
    )


def _missing_upstream_error(
    provider_name: str,
    status_code: int | None,
) -> ProviderError:
    """Return a typed error for malformed Scrapfly proxy payloads."""
    return ProviderError(
        ErrorType.PROVIDER_ERROR,
        "Scrapfly proxy returned no upstream response "
        f"(status_code={status_code})",
        provider_name,
    )


async def proxy_post_via_scrapfly(
    client: httpx.AsyncClient,
    request: ScrapflyPostRequest,
) -> ProxiedResponse:
    """Forward a POST request through Scrapfly and return the upstream body."""
    scrapfly_params = _build_scrapfly_params(
        request.target_url,
        request.target_headers,
        request.scrapfly_api_key,
    )
    data = await http_json(
        client,
        request.provider_name,
        _SCRAPFLY_URL,
        model=_ScrapflyResponse,
        method="POST",
        params=scrapfly_params,
        headers={
            "Content-Type": request.target_headers.get(
                "Content-Type",
                "application/json",
            )
        },
        content=request.target_body,
        timeout_s=request.timeout_ms / 1000,
    )

    upstream = data.result
    if upstream is None:
        raise _missing_upstream_error(request.provider_name, None)
    if upstream.status_code is None:
        raise _missing_upstream_error(request.provider_name, None)
    if upstream.content is None:
        raise _missing_upstream_error(
            request.provider_name,
            upstream.status_code,
        )
    return ProxiedResponse(
        status=upstream.status_code,
        body=upstream.content,
        headers=upstream.response_headers,
    )
