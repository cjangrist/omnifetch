"""Tests for the Jina fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.jina as jina_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.jina import JinaFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_JINA_URL = "https://r.jina.ai/"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_jina_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer jina-secret"
            assert request.headers["Content-Type"] == "application/json"
            assert request.headers["Accept"] == "application/json"
            assert request.headers["X-Return-Format"] == "markdown"
            assert _json_request(request) == {"url": _TARGET_URL}
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "title": "Jina",
                        "url": "https://canonical.example/article",
                        "content": "# Jina\n\nBody",
                        "usage": {"tokens": 123},
                    },
                },
                request=request,
            )

        router.post(_JINA_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = JinaFetchProvider(
                ProviderSecrets({"JINA_API_KEY": "jina-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="Jina",
        content="# Jina\n\nBody",
        source_provider="jina",
        metadata={"tokens": 123},
    )


@pytest.mark.parametrize("usage", [None, {"tokens": 0}])
async def test_jina_uses_fallbacks_without_token_metadata(
    usage: dict[str, int] | None,
) -> None:
    payload: dict[str, object] = {
        "code": 200,
        "data": {"content": "# Fallback\n\nBody"},
    }
    data = cast(dict[str, object], payload["data"])
    if usage is not None:
        data["usage"] = usage

    with respx.mock(assert_all_called=True) as router:
        router.post(_JINA_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = JinaFetchProvider(
                ProviderSecrets({"JINA_API_KEY": "jina-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="",
        content="# Fallback\n\nBody",
        source_provider="jina",
    )


async def test_jina_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = JinaFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for jina"


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 200},
        {"code": 200, "data": {"content": ""}},
    ],
)
async def test_jina_rejects_missing_content(
    payload: dict[str, object],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_JINA_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = JinaFetchProvider(
                ProviderSecrets({"JINA_API_KEY": "jina-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Jina Reader returned no content"
    )


def test_jina_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(jina_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"JINA_API_KEY": "jina-secret"})
    ) == ["jina"]
