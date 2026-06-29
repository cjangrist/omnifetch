"""Tests for the Linkup fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.linkup as linkup_module
from omnifetch.fetch.providers import (
    base,
    get_active_fetch_providers,
    UnifiedFetchProvider,
)
from omnifetch.fetch.providers.linkup import LinkupFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_FETCH_URL = "https://api.linkup.so/v1/fetch"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_linkup_fetches_markdown() -> None:
    """Linkup maps a markdown response to a normalized fetch result."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer linkup-secret"
            assert request.headers["Content-Type"] == "application/json"
            assert _json_request(request) == {
                "url": "https://example.test/article"
            }
            return httpx.Response(
                200,
                json={"markdown": "# Linkup\n\nBody"},
                request=request,
            )

        router.post(_FETCH_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = LinkupFetchProvider(
                ProviderSecrets({"LINKUP_API_KEY": "linkup-secret"}),
                client,
            )
            result = await provider.fetch_url("https://example.test/article")

    assert result == FetchResult(
        url="https://example.test/article",
        title="Linkup",
        content="# Linkup\n\nBody",
        source_provider="linkup",
    )


async def test_linkup_requires_key() -> None:
    """Linkup requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = LinkupFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for linkup"


@pytest.mark.parametrize(
    "payload",
    [
        {"markdown": ""},
        {},
    ],
)
async def test_linkup_rejects_empty_markdown(
    payload: dict[str, object],
) -> None:
    """Empty Linkup content becomes a normalized provider error."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = LinkupFetchProvider(
                ProviderSecrets({"LINKUP_API_KEY": "linkup-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Linkup returned no markdown content"
    )


async def test_linkup_maps_unauthorized_response() -> None:
    """Linkup HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = LinkupFetchProvider(
                ProviderSecrets({"LINKUP_API_KEY": "linkup-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


async def test_linkup_maps_rate_limit_response() -> None:
    """Linkup 429 responses remain typed rate-limit errors."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(429, json={"message": "slow down"})
        async with httpx.AsyncClient() as client:
            provider = LinkupFetchProvider(
                ProviderSecrets({"LINKUP_API_KEY": "linkup-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.RATE_LIMIT
    assert str(error_info.value) == "Rate limit exceeded for linkup"


async def test_unified_dispatcher_uses_linkup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unified dispatcher exposes Linkup when keyed."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(linkup_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(
            200,
            json={"markdown": "# Linkup\n\nBody"},
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"LINKUP_API_KEY": "linkup-secret"}),
                client,
            )
            result = await unified.fetch_url(
                "https://example.test/article",
                provider="linkup",
            )

    assert unified.active_names == ["linkup"]
    assert result.source_provider == "linkup"


def test_linkup_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linkup self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(linkup_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"LINKUP_API_KEY": "linkup-secret"})
    ) == ["linkup"]
