"""Tests for the Scrapfly fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrapfly as scrapfly_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.scrapfly import ScrapflyFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_SCRAPE_URL = "https://api.scrapfly.io/scrape"
_TARGET_URL = "https://example.test/article"


async def test_scrapfly_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["key"] == "scrapfly-secret"
            assert request.url.params["url"] == _TARGET_URL
            assert request.url.params["format"] == "markdown"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "content": "# Scrapfly\n\nBody",
                        "status_code": 200,
                        "url": "https://canonical.example/article",
                        "format": "markdown",
                    },
                    "config": {},
                },
                request=request,
            )

        router.get(_SCRAPE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrapflyFetchProvider(
                ProviderSecrets({"SCRAPFLY_API_KEY": "scrapfly-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Scrapfly",
        content="# Scrapfly\n\nBody",
        source_provider="scrapfly",
        metadata={"status_code": 200},
    )


async def test_scrapfly_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = ScrapflyFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrapfly"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": {"content": "", "status_code": 200}},
    ],
)
async def test_scrapfly_rejects_empty_results(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_SCRAPE_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = ScrapflyFetchProvider(
                ProviderSecrets({"SCRAPFLY_API_KEY": "scrapfly-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Scrapfly returned empty content"
    )


@pytest.mark.parametrize(
    ("status_code", "error_type", "message"),
    [
        (401, ErrorType.API_ERROR, "Invalid API key"),
        (429, ErrorType.RATE_LIMIT, "Rate limit exceeded for scrapfly"),
    ],
)
async def test_scrapfly_maps_http_errors(
    status_code: int,
    error_type: ErrorType,
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_SCRAPE_URL).respond(
            status_code,
            json={"message": message},
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapflyFetchProvider(
                ProviderSecrets({"SCRAPFLY_API_KEY": "scrapfly-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_scrapfly_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapfly_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPFLY_API_KEY": "scrapfly-secret"})
    ) == ["scrapfly"]
