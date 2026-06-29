"""Tests for the Olostep fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.olostep as olostep_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.olostep import OlostepFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_SCRAPES_URL = "https://api.olostep.com/v1/scrapes"
_TARGET_URL = "https://example.test/article"
_EMPTY_CONTENT_MESSAGE = (
    "Failed to fetch URL content: Olostep returned empty markdown content"
)


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_olostep_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer olostep-secret"
            assert _json_request(request) == {
                "url": _TARGET_URL,
                "formats": ["markdown"],
            }
            return httpx.Response(
                200,
                json={
                    "result": {
                        "markdown_content": "# Olostep\n\nBody",
                        "html_content": "<h1>Olostep</h1>",
                        "markdown_hosted_url": "https://files.test/page.md",
                    }
                },
                request=request,
            )

        router.post(_SCRAPES_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = OlostepFetchProvider(
                ProviderSecrets({"OLOSTEP_API_KEY": "olostep-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Olostep",
        content="# Olostep\n\nBody",
        source_provider="olostep",
    )


async def test_olostep_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = OlostepFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for olostep"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": {}},
        {"result": {"markdown_content": ""}},
        {"result": {"markdown_hosted_url": "https://files.test/page.md"}},
    ],
)
async def test_olostep_rejects_empty_markdown_content(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPES_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = OlostepFetchProvider(
                ProviderSecrets({"OLOSTEP_API_KEY": "olostep-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == _EMPTY_CONTENT_MESSAGE


async def test_olostep_maps_http_errors() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPES_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = OlostepFetchProvider(
                ProviderSecrets({"OLOSTEP_API_KEY": "olostep-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_olostep_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(olostep_module)

    assert OlostepFetchProvider.name == "olostep"
    assert OlostepFetchProvider.base_url == "https://api.olostep.com"
    assert OlostepFetchProvider.timeout_ms == 30_000
    assert OlostepFetchProvider.required_secrets == ("OLOSTEP_API_KEY",)
    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"OLOSTEP_API_KEY": "olostep-secret"})
    ) == ["olostep"]
