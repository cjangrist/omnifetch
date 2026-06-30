"""Tests for the Scrape.do fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrapedo as scrapedo_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.scrapedo import ScrapeDoFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://api.scrape.do"
_TARGET_URL = "https://example.test/article?topic=python&sort=recent"


async def test_scrapedo_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["token"] == "scrapedo-secret"
            assert request.url.params["url"] == _TARGET_URL
            assert request.url.params["output"] == "markdown"
            return httpx.Response(
                200,
                text="# Article\n\nBody",
                request=request,
            )

        router.get(_BASE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrapeDoFetchProvider(
                ProviderSecrets({"SCRAPE_DO_API_TOKEN": "scrapedo-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Article",
        content="# Article\n\nBody",
        source_provider="scrapedo",
    )


async def test_scrapedo_requires_token() -> None:
    async with httpx.AsyncClient() as client:
        provider = ScrapeDoFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrapedo"


async def test_scrapedo_rejects_empty_content() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(200, text="")
        async with httpx.AsyncClient() as client:
            provider = ScrapeDoFetchProvider(
                ProviderSecrets({"SCRAPE_DO_API_TOKEN": "scrapedo-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Scrape.do returned empty content"
    )


@pytest.mark.parametrize(
    ("status_code", "payload", "error_type", "message"),
    [
        (401, {"message": "bad token"}, ErrorType.API_ERROR, "Invalid API key"),
        (
            403,
            {"message": "forbidden"},
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
        ),
        (
            404,
            {"message": "gone"},
            ErrorType.API_ERROR,
            "scrapedo endpoint not found (404): gone",
        ),
        (
            429,
            {"message": "too many requests"},
            ErrorType.RATE_LIMIT,
            "Rate limit exceeded for scrapedo",
        ),
        (
            500,
            {"message": "down"},
            ErrorType.PROVIDER_ERROR,
            "scrapedo API internal error (500): down",
        ),
    ],
)
async def test_scrapedo_maps_http_errors(
    status_code: int,
    payload: dict[str, str],
    error_type: ErrorType,
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(status_code, json=payload)
        async with httpx.AsyncClient() as client:
            provider = ScrapeDoFetchProvider(
                ProviderSecrets({"SCRAPE_DO_API_TOKEN": "scrapedo-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_scrapedo_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapedo_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPE_DO_API_TOKEN": "scrapedo-secret"})
    ) == ["scrapedo"]
