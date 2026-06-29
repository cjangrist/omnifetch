"""Tests for the ScrapingBee fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrapingbee as scrapingbee_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.scrapingbee import ScrapingBeeFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_API_URL = "https://app.scrapingbee.com/api/v1"
_TARGET_URL = "https://example.test/article"


async def test_scrapingbee_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["api_key"] == "scrapingbee-secret"
            assert request.url.params["url"] == _TARGET_URL
            assert request.url.params["render_js"] == "false"
            assert request.url.params["return_page_markdown"] == "true"
            return httpx.Response(
                200,
                text="# Article\n\nBody",
                request=request,
            )

        router.get(_API_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrapingBeeFetchProvider(
                ProviderSecrets({"SCRAPINGBEE_API_KEY": "scrapingbee-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Article",
        content="# Article\n\nBody",
        source_provider="scrapingbee",
    )


async def test_scrapingbee_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = ScrapingBeeFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrapingbee"


async def test_scrapingbee_rejects_empty_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_API_URL).respond(200, text="")
        async with httpx.AsyncClient() as client:
            provider = ScrapingBeeFetchProvider(
                ProviderSecrets({"SCRAPINGBEE_API_KEY": "scrapingbee-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: ScrapingBee returned empty markdown"
    )


async def test_scrapingbee_maps_http_errors() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_API_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = ScrapingBeeFetchProvider(
                ProviderSecrets({"SCRAPINGBEE_API_KEY": "scrapingbee-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_scrapingbee_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapingbee_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPINGBEE_API_KEY": "scrapingbee-secret"})
    ) == ["scrapingbee"]


async def test_unified_dispatcher_uses_scrapingbee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapingbee_module)

    with respx.mock(assert_all_called=True) as router:
        router.get(_API_URL).respond(200, text="# Article\n\nBody")
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"SCRAPINGBEE_API_KEY": "scrapingbee-secret"}),
                client,
            )
            result = await unified.fetch_url(
                _TARGET_URL,
                provider="scrapingbee",
            )

    assert unified.active_names == ["scrapingbee"]
    assert result.source_provider == "scrapingbee"
