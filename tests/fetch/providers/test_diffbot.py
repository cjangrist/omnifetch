"""Tests for the Diffbot fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.diffbot as diffbot_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.diffbot import DiffbotFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_ARTICLE_URL = "https://example.test/article?campaign=fetch"
_DIFFBOT_URL = "https://api.diffbot.com/v3/article"


async def test_diffbot_fetches_article_text() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["token"] == "diffbot-secret"
            assert request.url.params["url"] == _ARTICLE_URL
            return httpx.Response(
                200,
                json={
                    "objects": [
                        {
                            "title": "Diffbot Article",
                            "text": "Article body",
                            "author": "Ada Lovelace",
                            "date": "2026-06-29",
                            "siteName": "Example News",
                            "images": [
                                {"url": "https://example.test/one.jpg"},
                                {
                                    "url": "https://example.test/two.jpg",
                                    "caption": "Second image",
                                },
                            ],
                        }
                    ]
                },
                request=request,
            )

        router.get(_DIFFBOT_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = DiffbotFetchProvider(
                ProviderSecrets({"DIFFBOT_TOKEN": "diffbot-secret"}),
                client,
            )
            result = await provider.fetch_url(_ARTICLE_URL)

    assert result == FetchResult(
        url=_ARTICLE_URL,
        title="Diffbot Article",
        content="Article body",
        source_provider="diffbot",
        metadata={
            "author": "Ada Lovelace",
            "date": "2026-06-29",
            "site_name": "Example News",
            "image_count": 2,
        },
    )


async def test_diffbot_omits_empty_optional_metadata() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_DIFFBOT_URL).respond(
            200,
            json={"objects": [{"text": "Plain article body"}]},
        )
        async with httpx.AsyncClient() as client:
            provider = DiffbotFetchProvider(
                ProviderSecrets({"DIFFBOT_TOKEN": "diffbot-secret"}),
                client,
            )
            result = await provider.fetch_url(_ARTICLE_URL)

    assert result == FetchResult(
        url=_ARTICLE_URL,
        title="",
        content="Plain article body",
        source_provider="diffbot",
    )


async def test_diffbot_requires_token() -> None:
    async with httpx.AsyncClient() as client:
        provider = DiffbotFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_ARTICLE_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for diffbot"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"objects": []},
        {"objects": [{}]},
        {"objects": [{"text": ""}]},
    ],
)
async def test_diffbot_rejects_missing_article_text(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_DIFFBOT_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = DiffbotFetchProvider(
                ProviderSecrets({"DIFFBOT_TOKEN": "diffbot-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_ARTICLE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Diffbot returned no article content"
    )


@pytest.mark.parametrize(
    ("status_code", "payload", "error_type", "message"),
    [
        (
            401,
            {"message": "bad token"},
            ErrorType.API_ERROR,
            "Invalid API key",
        ),
        (
            429,
            {"message": "too many requests"},
            ErrorType.RATE_LIMIT,
            "Rate limit exceeded for diffbot",
        ),
    ],
)
async def test_diffbot_maps_http_errors(
    status_code: int,
    payload: dict[str, str],
    error_type: ErrorType,
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_DIFFBOT_URL).respond(status_code, json=payload)
        async with httpx.AsyncClient() as client:
            provider = DiffbotFetchProvider(
                ProviderSecrets({"DIFFBOT_TOKEN": "diffbot-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_ARTICLE_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_diffbot_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(diffbot_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"DIFFBOT_TOKEN": "diffbot-secret"})
    ) == ["diffbot"]
