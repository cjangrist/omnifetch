"""Tests for the ScrapingAnt fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrapingant as scrapingant_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.scrapingant import ScrapingAntFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://api.scrapingant.com"
_TARGET_URL = "https://example.test/article"


async def test_scrapingant_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["url"] == _TARGET_URL
            assert request.url.params["x-api-key"] == "scrapingant-secret"
            return httpx.Response(
                200,
                json={
                    "url": "https://canonical.example/article",
                    "markdown": "# ScrapingAnt\n\nBody",
                },
                request=request,
            )

        router.get(f"{_BASE_URL}/v2/markdown").mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrapingAntFetchProvider(
                ProviderSecrets({"SCRAPINGANT_API_KEY": "scrapingant-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="ScrapingAnt",
        content="# ScrapingAnt\n\nBody",
        source_provider="scrapingant",
    )


async def test_scrapingant_uses_requested_url_fallback() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_BASE_URL}/v2/markdown").respond(
            json={"markdown": "Plain markdown body"}
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapingAntFetchProvider(
                ProviderSecrets({"SCRAPINGANT_API_KEY": "scrapingant-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="",
        content="Plain markdown body",
        source_provider="scrapingant",
    )


async def test_scrapingant_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = ScrapingAntFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrapingant"


async def test_scrapingant_rejects_empty_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_BASE_URL}/v2/markdown").respond(
            json={"url": _TARGET_URL, "markdown": ""}
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapingAntFetchProvider(
                ProviderSecrets({"SCRAPINGANT_API_KEY": "scrapingant-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: ScrapingAnt returned no markdown content"
    )


@pytest.mark.parametrize(
    ("status_code", "error_type", "message"),
    [
        (401, ErrorType.API_ERROR, "Invalid API key"),
        (429, ErrorType.RATE_LIMIT, "Rate limit exceeded for scrapingant"),
        (
            500,
            ErrorType.PROVIDER_ERROR,
            "scrapingant API internal error (500): down",
        ),
    ],
)
async def test_scrapingant_maps_http_errors(
    status_code: int,
    error_type: ErrorType,
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_BASE_URL}/v2/markdown").respond(
            status_code,
            json={"message": "down"},
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapingAntFetchProvider(
                ProviderSecrets({"SCRAPINGANT_API_KEY": "scrapingant-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_scrapingant_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapingant_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPINGANT_API_KEY": "scrapingant-secret"})
    ) == ["scrapingant"]
