"""Tests for the Decodo fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.decodo as decodo_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.decodo import DecodoFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://scraper-api.decodo.com/v2/scrape"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_decodo_fetches_markdown() -> None:
    """Decodo sends the expected scrape request and maps markdown content."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Accept"] == "application/json"
            assert request.headers["Authorization"] == "Basic encoded-token"
            assert request.headers["Content-Type"] == "application/json"
            assert _json_request(request) == {
                "url": _TARGET_URL,
                "markdown": True,
            }
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "content": "# Decodo\n\nBody",
                            "status_code": 200,
                            "task_id": "task-1",
                        }
                    ]
                },
                request=request,
            )

        router.post(_BASE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = DecodoFetchProvider(
                ProviderSecrets(
                    {"DECODO_WEB_SCRAPING_API_KEY": "encoded-token"}
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Decodo",
        content="# Decodo\n\nBody",
        source_provider="decodo",
    )


async def test_decodo_requires_key() -> None:
    """Decodo requires its provider-owned Basic auth token."""
    async with httpx.AsyncClient() as client:
        provider = DecodoFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for decodo"


async def test_decodo_returns_markdown_despite_target_status_code() -> None:
    """Decodo parity follows content presence, not scraped target status."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BASE_URL).respond(
            200,
            json={
                "results": [
                    {
                        "content": "# Access Denied\n\nBody",
                        "status_code": 404,
                        "task_id": "task-404",
                    }
                ]
            },
        )
        async with httpx.AsyncClient() as client:
            provider = DecodoFetchProvider(
                ProviderSecrets(
                    {"DECODO_WEB_SCRAPING_API_KEY": "encoded-token"}
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Access Denied",
        content="# Access Denied\n\nBody",
        source_provider="decodo",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"results": []},
        {"results": [{"content": ""}]},
    ],
)
async def test_decodo_rejects_empty_results(
    payload: dict[str, object],
) -> None:
    """Empty Decodo result arrays and content become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BASE_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = DecodoFetchProvider(
                ProviderSecrets(
                    {"DECODO_WEB_SCRAPING_API_KEY": "encoded-token"}
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Decodo returned empty content"
    )


@pytest.mark.parametrize(
    ("status_code", "response_message", "error_type", "message"),
    [
        (401, "bad key", ErrorType.API_ERROR, "Invalid API key"),
        (
            403,
            "forbidden",
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
        ),
        (
            404,
            "missing",
            ErrorType.API_ERROR,
            "decodo endpoint not found (404): missing",
        ),
        (
            429,
            "too many",
            ErrorType.RATE_LIMIT,
            "Rate limit exceeded for decodo",
        ),
        (
            503,
            "down",
            ErrorType.PROVIDER_ERROR,
            "decodo API internal error (503): down",
        ),
    ],
)
async def test_decodo_maps_http_errors(
    status_code: int,
    response_message: str,
    error_type: ErrorType,
    message: str,
) -> None:
    """Decodo HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BASE_URL).respond(
            status_code,
            json={"message": response_message},
        )
        async with httpx.AsyncClient() as client:
            provider = DecodoFetchProvider(
                ProviderSecrets(
                    {"DECODO_WEB_SCRAPING_API_KEY": "encoded-token"}
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_decodo_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decodo self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(decodo_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"DECODO_WEB_SCRAPING_API_KEY": "encoded-token"})
    ) == ["decodo"]
