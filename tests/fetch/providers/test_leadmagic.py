"""Tests for the LeadMagic fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.leadmagic as leadmagic_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.leadmagic import LeadMagicFetchProvider
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_SCRAPE_URL = "https://api.web2md.app/api/scrape"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_leadmagic_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-API-Key"] == "leadmagic-secret"
            assert _json_request(request) == {"url": _TARGET_URL}
            return httpx.Response(
                200,
                json={
                    "markdown": "# Upstream Heading\n\nBody",
                    "title": "LeadMagic Title",
                    "url": "https://canonical.example/article",
                },
                request=request,
            )

        router.post(_SCRAPE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = LeadMagicFetchProvider(
                ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="LeadMagic Title",
        content="# Upstream Heading\n\nBody",
        source_provider="leadmagic",
    )


async def test_leadmagic_uses_markdown_title_fallback() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={"markdown": "# Fallback Heading\n\nBody", "title": ""}
        )
        async with httpx.AsyncClient() as client:
            provider = LeadMagicFetchProvider(
                ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.title == "Fallback Heading"


async def test_leadmagic_accepts_nested_payload_shape() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={
                "success": True,
                "data": {
                    "markdown": "# Nested Heading\n\nBody",
                    "title": "Nested Title",
                    "url": "https://canonical.example/article",
                },
            }
        )
        async with httpx.AsyncClient() as client:
            provider = LeadMagicFetchProvider(
                ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Nested Title",
        content="# Nested Heading\n\nBody",
        source_provider="leadmagic",
    )


async def test_leadmagic_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = LeadMagicFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for leadmagic"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"markdown": ""},
        {"data": {"markdown": ""}},
    ],
)
async def test_leadmagic_rejects_empty_markdown(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = LeadMagicFetchProvider(
                ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: LeadMagic returned empty markdown"
    )


async def test_leadmagic_maps_http_errors() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = LeadMagicFetchProvider(
                ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_leadmagic_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(leadmagic_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"})
    ) == ["leadmagic"]


async def test_unified_dispatcher_uses_leadmagic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(leadmagic_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={"markdown": "# LeadMagic\n\nBody"}
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"LEADMAGIC_API_KEY": "leadmagic-secret"}),
                client,
            )
            result = await unified.fetch_url(
                _TARGET_URL,
                provider="leadmagic",
            )

    assert unified.active_names == ["leadmagic"]
    assert result.source_provider == "leadmagic"
