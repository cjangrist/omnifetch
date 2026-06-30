"""Tests for the OpenGraph.io fetch provider."""

from __future__ import annotations

import importlib
from urllib.parse import quote

import httpx
import pytest
import respx

import omnifetch.fetch.providers.opengraph as opengraph_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.opengraph import OpenGraphFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://opengraph.io"
_TARGET_URL = "https://example.test/articles/open-graph?ref=unit"
_EXTRACT_URL = f"{_BASE_URL}/api/1.1/extract/{quote(_TARGET_URL, safe='')}"


async def test_opengraph_fetches_concatenated_text() -> None:
    """OpenGraph.io prefers concatenatedText when present."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["app_id"] == "opengraph-secret"
            return httpx.Response(
                200,
                json={
                    "tags": [
                        {
                            "tag": "title",
                            "innerText": "OpenGraph Title",
                            "position": 1,
                        },
                        {"tag": "p", "innerText": "Ignored fallback"},
                    ],
                    "concatenatedText": "Primary extracted content",
                    "requestInfo": {
                        "host": "example.test",
                        "responseCode": 200,
                    },
                },
                request=request,
            )

        router.get(_EXTRACT_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = OpenGraphFetchProvider(
                ProviderSecrets({"OPENGRAPH_IO_API_KEY": "opengraph-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="OpenGraph Title",
        content="Primary extracted content",
        source_provider="opengraph",
        metadata={"response_code": 200, "tag_count": 2},
    )


async def test_opengraph_falls_back_to_tag_text() -> None:
    """OpenGraph.io joins tag inner text when concatenatedText is empty."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_EXTRACT_URL).respond(
            json={
                "tags": [
                    {"tag": "p", "innerText": "First paragraph"},
                    {"tag": "div", "innerText": "Second paragraph"},
                ],
                "concatenatedText": "",
            }
        )
        async with httpx.AsyncClient() as client:
            provider = OpenGraphFetchProvider(
                ProviderSecrets({"OPENGRAPH_IO_API_KEY": "opengraph-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="",
        content="First paragraph\n\nSecond paragraph",
        source_provider="opengraph",
        metadata={"response_code": None, "tag_count": 2},
    )


async def test_opengraph_requires_key() -> None:
    """OpenGraph.io requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = OpenGraphFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for opengraph"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"tags": [], "concatenatedText": ""},
            "Failed to fetch URL content: "
            "OpenGraph.io returned empty extraction",
        ),
        (
            {"tags": [{"tag": "p", "innerText": ""}]},
            "Failed to fetch URL content: OpenGraph.io returned empty content",
        ),
    ],
)
async def test_opengraph_rejects_empty_extraction(
    payload: dict[str, object],
    message: str,
) -> None:
    """OpenGraph.io empty extraction payloads become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_EXTRACT_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = OpenGraphFetchProvider(
                ProviderSecrets({"OPENGRAPH_IO_API_KEY": "opengraph-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_opengraph_maps_http_errors() -> None:
    """OpenGraph.io HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_EXTRACT_URL).respond(401, json={"message": "bad app id"})
        async with httpx.AsyncClient() as client:
            provider = OpenGraphFetchProvider(
                ProviderSecrets({"OPENGRAPH_IO_API_KEY": "opengraph-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_opengraph_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenGraph.io self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(opengraph_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"OPENGRAPH_IO_API_KEY": "opengraph-secret"})
    ) == ["opengraph"]
