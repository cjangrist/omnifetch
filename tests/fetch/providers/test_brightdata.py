"""Tests for the Bright Data fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.brightdata as brightdata_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.brightdata import (
    _resolve_zone,
    BrightDataFetchProvider,
)
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BRIGHT_DATA_URL = "https://api.brightdata.com/request"
_TARGET_URL = "https://example.test/article"
_CONTENT = "# Bright Data\n\nExtracted markdown body."


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_resolve_zone_defaults_for_missing_or_blank_values() -> None:
    """Missing or blank optional zones use the Bright Data source default."""
    assert _resolve_zone(None) == "unblocker"
    assert _resolve_zone("") == "unblocker"
    assert _resolve_zone("   ") == "unblocker"
    assert _resolve_zone(" custom-zone ") == "custom-zone"


async def test_brightdata_fetches_markdown_with_default_zone() -> None:
    """Bright Data posts the Web Unlocker request and returns markdown."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer bright-secret"
            assert request.headers["Content-Type"] == "application/json"
            assert _json_request(request) == {
                "zone": "unblocker",
                "url": _TARGET_URL,
                "format": "raw",
                "data_format": "markdown",
            }
            return httpx.Response(200, text=_CONTENT, request=request)

        router.post(_BRIGHT_DATA_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = BrightDataFetchProvider(
                ProviderSecrets({"BRIGHT_DATA_API_KEY": "bright-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Bright Data",
        content=_CONTENT,
        source_provider="brightdata",
    )


async def test_brightdata_uses_configured_zone() -> None:
    """Bright Data preserves the optional zone override in the request body."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert _json_request(request)["zone"] == "custom-zone"
            return httpx.Response(200, text=_CONTENT, request=request)

        router.post(_BRIGHT_DATA_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = BrightDataFetchProvider(
                ProviderSecrets(
                    {
                        "BRIGHT_DATA_API_KEY": "bright-secret",
                        "BRIGHT_DATA_ZONE": "custom-zone",
                    }
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.content == _CONTENT


async def test_brightdata_requires_key() -> None:
    """Bright Data requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = BrightDataFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for brightdata"


async def test_brightdata_rejects_empty_markdown() -> None:
    """Empty Bright Data markdown responses become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BRIGHT_DATA_URL).respond(200, text="")
        async with httpx.AsyncClient() as client:
            provider = BrightDataFetchProvider(
                ProviderSecrets({"BRIGHT_DATA_API_KEY": "bright-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Bright Data returned empty markdown"
    )


async def test_brightdata_maps_http_errors() -> None:
    """Bright Data HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BRIGHT_DATA_URL).respond(
            401,
            json={"message": "bad key"},
        )
        async with httpx.AsyncClient() as client:
            provider = BrightDataFetchProvider(
                ProviderSecrets({"BRIGHT_DATA_API_KEY": "bright-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


async def test_unified_dispatcher_uses_brightdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unified dispatcher can call an active Bright Data provider."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(brightdata_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_BRIGHT_DATA_URL).respond(200, text=_CONTENT)
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"BRIGHT_DATA_API_KEY": "bright-secret"}),
                client,
            )
            result = await unified.fetch_url(_TARGET_URL, provider="brightdata")

    assert unified.active_names == ["brightdata"]
    assert result.source_provider == "brightdata"


def test_brightdata_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bright Data self-registers and follows exact key availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(brightdata_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"BRIGHT_DATA_API_KEY": "bright-secret"})
    ) == ["brightdata"]
