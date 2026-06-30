"""Tests for the Scrapeless fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrapeless as scrapeless_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.scrapeless import ScrapelessFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_UNLOCKER_URL = "https://api.scrapeless.com/api/v2/unlocker/request"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_scrapeless_fetches_markdown() -> None:
    """Scrapeless sends the unlocker request and maps markdown content."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-token"] == "scrapeless-secret"
            assert _json_request(request) == {
                "actor": "unlocker.webunlocker",
                "input": {
                    "url": _TARGET_URL,
                    "method": "GET",
                    "redirect": False,
                    "jsRender": {
                        "enabled": True,
                        "response": {"type": "markdown"},
                    },
                },
                "proxy": {"country": "ANY"},
            }
            return httpx.Response(
                200,
                json={"code": 200, "data": "# Article\n\nBody"},
                request=request,
            )

        router.post(_UNLOCKER_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrapelessFetchProvider(
                ProviderSecrets({"SCRAPELESS_API_KEY": "scrapeless-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Article",
        content="# Article\n\nBody",
        source_provider="scrapeless",
    )


async def test_scrapeless_requires_key() -> None:
    """Scrapeless requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = ScrapelessFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrapeless"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"code": 500, "data": "# Error"},
            "Failed to fetch URL content: Scrapeless returned code 500",
        ),
        (
            {"code": 200, "data": ""},
            "Failed to fetch URL content: Scrapeless returned empty data",
        ),
    ],
)
async def test_scrapeless_rejects_failed_or_empty_response(
    payload: dict[str, object],
    message: str,
) -> None:
    """Scrapeless requires a successful code and non-empty markdown."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_UNLOCKER_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = ScrapelessFetchProvider(
                ProviderSecrets({"SCRAPELESS_API_KEY": "scrapeless-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


@pytest.mark.parametrize(
    ("status_code", "error_type", "message"),
    [
        (401, ErrorType.API_ERROR, "Invalid API key"),
        (429, ErrorType.RATE_LIMIT, "Rate limit exceeded for scrapeless"),
    ],
)
async def test_scrapeless_maps_http_errors(
    status_code: int,
    error_type: ErrorType,
    message: str,
) -> None:
    """Scrapeless HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_UNLOCKER_URL).respond(
            status_code, json={"message": "bad key"}
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapelessFetchProvider(
                ProviderSecrets({"SCRAPELESS_API_KEY": "scrapeless-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_scrapeless_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrapeless self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapeless_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPELESS_API_KEY": "scrapeless-secret"})
    ) == ["scrapeless"]


async def test_unified_dispatcher_uses_scrapeless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unified dispatcher can call an active Scrapeless provider."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapeless_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_UNLOCKER_URL).respond(
            json={"code": 200, "data": "# Scrapeless\n\nBody"}
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"SCRAPELESS_API_KEY": "scrapeless-secret"}),
                client,
            )
            result = await unified.fetch_url(
                _TARGET_URL,
                provider="scrapeless",
            )

    assert unified.active_names == ["scrapeless"]
    assert result.source_provider == "scrapeless"
