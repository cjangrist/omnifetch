"""Tests for the Zyte fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.zyte as zyte_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.zyte import ZyteFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_EXTRACT_URL = "https://api.zyte.com/v1/extract"
_TARGET_URL = "https://example.test/article"
_NO_CONTENT_MESSAGE = (
    "Failed to fetch URL content: Zyte returned no page content"
)


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_zyte_fetches_page_content() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Basic enl0ZS1zZWNyZXQ6"
            assert _json_request(request) == {
                "url": _TARGET_URL,
                "pageContent": True,
            }
            return httpx.Response(
                200,
                json={
                    "url": "https://response.example/article",
                    "statusCode": 200,
                    "pageContent": {
                        "headline": "Extracted headline",
                        "title": "Extracted title",
                        "itemMain": "Main article body",
                        "canonicalUrl": "https://canonical.example/article",
                        "metadata": {"language": "en"},
                    },
                },
                request=request,
            )

        router.post(_EXTRACT_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="Extracted title",
        content="Main article body",
        source_provider="zyte",
        metadata={
            "headline": "Extracted headline",
            "zyte_metadata": {"language": "en"},
        },
    )


async def test_zyte_uses_headline_and_response_url_fallbacks() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_EXTRACT_URL).respond(
            json={
                "url": "https://response.example/article",
                "pageContent": {
                    "headline": "Fallback headline",
                    "itemMain": "Main article body",
                },
            }
        )
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url="https://response.example/article",
        title="Fallback headline",
        content="Main article body",
        source_provider="zyte",
        metadata={"headline": "Fallback headline"},
    )


async def test_zyte_uses_requested_url_without_optional_metadata() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_EXTRACT_URL).respond(
            json={"pageContent": {"itemMain": "Main article body"}}
        )
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="",
        content="Main article body",
        source_provider="zyte",
    )


async def test_zyte_preserves_present_empty_title_and_canonical_url() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_EXTRACT_URL).respond(
            json={
                "url": "https://response.example/article",
                "pageContent": {
                    "headline": "Fallback headline",
                    "title": "",
                    "itemMain": "Main article body",
                    "canonicalUrl": "",
                },
            }
        )
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.url == ""
    assert result.title == ""


async def test_zyte_preserves_present_empty_metadata() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_EXTRACT_URL).respond(
            json={
                "pageContent": {"itemMain": "Main article body", "metadata": {}}
            }
        )
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.metadata == {"zyte_metadata": {}}


async def test_zyte_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = ZyteFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for zyte"


@pytest.mark.parametrize(
    "payload",
    [
        {"url": "https://response.example/article"},
        {"pageContent": {}},
        {"pageContent": {"itemMain": ""}},
    ],
)
async def test_zyte_rejects_empty_or_missing_content(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_EXTRACT_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == _NO_CONTENT_MESSAGE


@pytest.mark.parametrize(
    ("status_code", "response_message", "error_type", "message"),
    [
        (401, "bad credentials", ErrorType.API_ERROR, "Invalid API key"),
        (
            403,
            "forbidden",
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
        ),
        (429, "too many", ErrorType.RATE_LIMIT, "Rate limit exceeded for zyte"),
        (
            503,
            "overloaded",
            ErrorType.PROVIDER_ERROR,
            "zyte API internal error (503): overloaded",
        ),
    ],
)
async def test_zyte_maps_http_errors(
    status_code: int,
    response_message: str,
    error_type: ErrorType,
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_EXTRACT_URL).respond(
            status_code, json={"message": response_message}
        )
        async with httpx.AsyncClient() as client:
            provider = ZyteFetchProvider(
                ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


async def test_zyte_maps_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        provider = ZyteFetchProvider(
            ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"}),
            client,
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == "connection refused"


def test_zyte_registers_and_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(zyte_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"ZYTE_API_KEY": "zyte-secret"})
    ) == ["zyte"]
