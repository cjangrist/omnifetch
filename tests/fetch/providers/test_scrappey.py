"""Tests for the Scrappey fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrappey as scrappey_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.scrappey import ScrappeyFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://publisher.scrappey.com/api/v1"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_scrappey_fetches_visible_text() -> None:
    """Scrappey maps solution.innerText and HTML title into FetchResult."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["key"] == "scrappey-secret"
            assert request.headers["Content-Type"].startswith(
                "application/json"
            )
            assert _json_request(request) == {
                "cmd": "request.get",
                "url": _TARGET_URL,
            }
            return httpx.Response(
                200,
                json={
                    "data": "success",
                    "solution": {
                        "innerText": "Visible page text",
                        "response": (
                            "<html><head><title>Example Title</title></head>"
                            "<body>Visible page text</body></html>"
                        ),
                        "currentUrl": "https://canonical.example/article",
                        "statusCode": 200,
                    },
                },
                request=request,
            )

        router.post(_BASE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrappeyFetchProvider(
                ProviderSecrets({"SCRAPPEY_API_KEY": "scrappey-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Example Title",
        content="Visible page text",
        source_provider="scrappey",
    )


async def test_scrappey_uses_empty_title_without_html_response() -> None:
    """Missing solution.response keeps the title empty."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BASE_URL).respond(
            json={
                "data": "success",
                "solution": {"innerText": "Visible page text"},
            }
        )
        async with httpx.AsyncClient() as client:
            provider = ScrappeyFetchProvider(
                ProviderSecrets({"SCRAPPEY_API_KEY": "scrappey-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.title == ""
    assert result.content == "Visible page text"


async def test_scrappey_requires_key() -> None:
    """Scrappey requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = ScrappeyFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrappey"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"data": "blocked", "solution": {"innerText": "Visible"}},
            "Failed to fetch URL content: Scrappey request failed: blocked",
        ),
        (
            {"data": "success"},
            "Failed to fetch URL content: Scrappey request failed: success",
        ),
        (
            {"solution": {"innerText": "Visible"}},
            "Failed to fetch URL content: "
            "Scrappey request failed: (missing data field)",
        ),
        (
            {"data": "success", "solution": {"innerText": ""}},
            "Failed to fetch URL content: Scrappey returned empty innerText",
        ),
        (
            {"data": "success", "solution": {}},
            "Failed to fetch URL content: Scrappey returned empty innerText",
        ),
    ],
)
async def test_scrappey_rejects_failed_or_empty_results(
    payload: dict[str, object],
    message: str,
) -> None:
    """Scrappey unsuccessful or empty payloads become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BASE_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = ScrappeyFetchProvider(
                ProviderSecrets({"SCRAPPEY_API_KEY": "scrappey-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_scrappey_maps_http_errors() -> None:
    """Scrappey HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_BASE_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = ScrappeyFetchProvider(
                ProviderSecrets({"SCRAPPEY_API_KEY": "scrappey-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_scrappey_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrappey self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrappey_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPPEY_API_KEY": "scrappey-secret"})
    ) == ["scrappey"]
