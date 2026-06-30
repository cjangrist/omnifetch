"""Tests for the Oxylabs fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.oxylabs as oxylabs_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.oxylabs import OxylabsFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_QUERY_URL = "https://realtime.oxylabs.io/v1/queries"
_TARGET_URL = "https://example.test/article"
_BASIC_AUTH_HEADER = "Basic b3h5LXVzZXI6b3h5LXBhc3M="


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _secrets(
    username: str = "oxy-user",
    password: str = "oxy-pass",
) -> ProviderSecrets:
    """Return configured Oxylabs provider secrets."""
    return ProviderSecrets(
        {
            "OXYLABS_WEB_SCRAPER_USERNAME": username,
            "OXYLABS_WEB_SCRAPER_PASSWORD": password,
        }
    )


async def test_oxylabs_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == _BASIC_AUTH_HEADER
            assert _json_request(request) == {
                "source": "universal",
                "url": _TARGET_URL,
                "markdown": True,
            }
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "content": "# Oxylabs Article\n\nBody",
                            "status_code": 200,
                        }
                    ]
                },
                request=request,
            )

        router.post(_QUERY_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = OxylabsFetchProvider(_secrets(), client)
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Oxylabs Article",
        content="# Oxylabs Article\n\nBody",
        source_provider="oxylabs",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"results": []},
        {"results": [{"content": ""}]},
    ],
)
async def test_oxylabs_rejects_empty_content(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_QUERY_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = OxylabsFetchProvider(_secrets(), client)
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Oxylabs returned empty content"
    )


@pytest.mark.parametrize(
    "secrets",
    [
        ProviderSecrets({}),
        ProviderSecrets({"OXYLABS_WEB_SCRAPER_USERNAME": "oxy-user"}),
    ],
)
async def test_oxylabs_requires_credentials(secrets: ProviderSecrets) -> None:
    async with httpx.AsyncClient() as client:
        provider = OxylabsFetchProvider(secrets, client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for oxylabs"


async def test_oxylabs_maps_http_errors() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_QUERY_URL).respond(
            401, json={"message": "bad credentials"}
        )
        async with httpx.AsyncClient() as client:
            provider = OxylabsFetchProvider(_secrets(), client)
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_oxylabs_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(oxylabs_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert (
        get_active_fetch_providers(
            ProviderSecrets({"OXYLABS_WEB_SCRAPER_USERNAME": "oxy-user"})
        )
        == []
    )
    assert (
        get_active_fetch_providers(
            ProviderSecrets({"OXYLABS_WEB_SCRAPER_PASSWORD": "oxy-pass"})
        )
        == []
    )
    assert get_active_fetch_providers(_secrets()) == ["oxylabs"]
