"""Tests for the You.com fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.you as you_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.you import YouFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_CONTENTS_URL = "https://ydc-index.io/v1/contents"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_you_fetches_markdown() -> None:
    """You.com maps the first Contents result to a fetch result."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-API-Key"] == "you-secret"
            assert _json_request(request) == {
                "urls": [_TARGET_URL],
                "formats": ["markdown"],
            }
            return httpx.Response(
                200,
                json=[
                    {
                        "url": "https://canonical.example/article",
                        "title": "You Article",
                        "markdown": "# You Article\n\nBody",
                    }
                ],
                request=request,
            )

        router.post(_CONTENTS_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = YouFetchProvider(
                ProviderSecrets({"YOU_API_KEY": "you-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="You Article",
        content="# You Article\n\nBody",
        source_provider="you",
    )


async def test_you_uses_url_and_title_fallbacks() -> None:
    """You.com falls back to the requested URL and empty title."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_CONTENTS_URL).respond(
            200,
            json=[{"markdown": "# Untitled\n\nBody"}],
        )
        async with httpx.AsyncClient() as client:
            provider = YouFetchProvider(
                ProviderSecrets({"YOU_API_KEY": "you-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="",
        content="# Untitled\n\nBody",
        source_provider="you",
    )


async def test_you_requires_key() -> None:
    """You.com requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = YouFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for you"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"url": _TARGET_URL, "title": "Empty", "markdown": None}],
        [{"url": _TARGET_URL, "title": "Empty", "markdown": ""}],
    ],
)
async def test_you_rejects_empty_or_missing_markdown(
    payload: list[dict[str, object]] | list[object],
) -> None:
    """You.com empty Contents responses become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_CONTENTS_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = YouFetchProvider(
                ProviderSecrets({"YOU_API_KEY": "you-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: You.com Contents returned no markdown"
    )


@pytest.mark.parametrize(
    ("status_code", "error_type", "message"),
    [
        (401, ErrorType.API_ERROR, "Invalid API key"),
        (429, ErrorType.RATE_LIMIT, "Rate limit exceeded for you"),
    ],
)
async def test_you_maps_http_errors(
    status_code: int,
    error_type: ErrorType,
    message: str,
) -> None:
    """You.com HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_CONTENTS_URL).respond(
            status_code,
            json={"message": "provider error"},
        )
        async with httpx.AsyncClient() as client:
            provider = YouFetchProvider(
                ProviderSecrets({"YOU_API_KEY": "you-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_you_registers_and_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """You.com self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(you_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"YOU_API_KEY": "you-secret"})
    ) == ["you"]


async def test_unified_dispatcher_uses_you(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unified dispatcher can explicitly call the You.com provider."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(you_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_CONTENTS_URL).respond(
            json=[
                {
                    "url": _TARGET_URL,
                    "title": "Dispatched",
                    "markdown": "# Dispatched\n\nBody",
                }
            ],
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"YOU_API_KEY": "you-secret"}),
                client,
            )
            result = await unified.fetch_url(_TARGET_URL, provider="you")

    assert unified.active_names == ["you"]
    assert result.source_provider == "you"
