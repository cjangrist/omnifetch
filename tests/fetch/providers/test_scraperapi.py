"""Tests for the ScraperAPI fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scraperapi as scraperapi_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.scraperapi import ScraperAPIFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://api.scraperapi.com"
_TARGET_URL = "https://example.test/article"


async def test_scraperapi_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["api_key"] == "scraperapi-secret"
            assert request.url.params["url"] == _TARGET_URL
            assert request.url.params["output_format"] == "markdown"
            return httpx.Response(
                200,
                text="# Article\n\nBody",
                request=request,
            )

        router.get(_BASE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScraperAPIFetchProvider(
                ProviderSecrets({"SCRAPERAPI_API_KEY": "scraperapi-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Article",
        content="# Article\n\nBody",
        source_provider="scraperapi",
    )


async def test_scraperapi_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = ScraperAPIFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scraperapi"


async def test_scraperapi_rejects_empty_content() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(200, text="")
        async with httpx.AsyncClient() as client:
            provider = ScraperAPIFetchProvider(
                ProviderSecrets({"SCRAPERAPI_API_KEY": "scraperapi-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: ScraperAPI returned empty content"
    )


async def test_scraperapi_maps_invalid_key_response() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = ScraperAPIFetchProvider(
                ProviderSecrets({"SCRAPERAPI_API_KEY": "scraperapi-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


async def test_scraperapi_maps_rate_limit_response() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(429, json={"message": "too many"})
        async with httpx.AsyncClient() as client:
            provider = ScraperAPIFetchProvider(
                ProviderSecrets({"SCRAPERAPI_API_KEY": "scraperapi-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.RATE_LIMIT
    assert str(error_info.value) == "Rate limit exceeded for scraperapi"


def test_scraperapi_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scraperapi_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPERAPI_API_KEY": "scraperapi-secret"})
    ) == ["scraperapi"]
