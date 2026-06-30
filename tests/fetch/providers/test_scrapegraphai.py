"""Tests for the ScrapeGraphAI fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.scrapegraphai as scrapegraphai_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.scrapegraphai import (
    ScrapeGraphAIFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_MARKDOWNIFY_URL = "https://api.scrapegraphai.com/v1/markdownify"
_TARGET_URL = "https://example.test/article"
_MARKDOWN = "# ScrapeGraphAI\n\nBody"
_NO_CONTENT_MESSAGE = (
    "Failed to fetch URL content: ScrapeGraphAI returned empty result"
)


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_scrapegraphai_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["SGAI-APIKEY"] == "scrapegraphai-secret"
            assert request.headers["Content-Type"] == "application/json"
            assert _json_request(request) == {"website_url": _TARGET_URL}
            return httpx.Response(
                200,
                json={
                    "request_id": "request-1",
                    "status": "completed",
                    "website_url": _TARGET_URL,
                    "result": _MARKDOWN,
                    "error": "",
                },
                request=request,
            )

        router.post(_MARKDOWNIFY_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = ScrapeGraphAIFetchProvider(
                ProviderSecrets(
                    {"SCRAPEGRAPHAI_API_KEY": "scrapegraphai-secret"}
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="ScrapeGraphAI",
        content=_MARKDOWN,
        source_provider="scrapegraphai",
        metadata={"request_id": "request-1"},
    )


async def test_scrapegraphai_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = ScrapeGraphAIFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for scrapegraphai"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "request_id": "request-1",
                "status": "failed",
                "website_url": _TARGET_URL,
                "result": None,
                "error": "blocked",
            },
            "Failed to fetch URL content: ScrapeGraphAI failed: blocked",
        ),
        (
            {
                "request_id": "request-1",
                "status": "failed",
                "website_url": _TARGET_URL,
                "result": None,
                "error": "",
            },
            "Failed to fetch URL content: ScrapeGraphAI failed: unknown error",
        ),
        (
            {
                "request_id": "request-1",
                "status": "completed",
                "website_url": _TARGET_URL,
                "result": "",
                "error": "",
            },
            _NO_CONTENT_MESSAGE,
        ),
        (
            {
                "request_id": "request-1",
                "status": "completed",
                "website_url": _TARGET_URL,
                "result": None,
                "error": "",
            },
            _NO_CONTENT_MESSAGE,
        ),
    ],
)
async def test_scrapegraphai_rejects_failed_or_empty_results(
    payload: dict[str, object],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_MARKDOWNIFY_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = ScrapeGraphAIFetchProvider(
                ProviderSecrets(
                    {"SCRAPEGRAPHAI_API_KEY": "scrapegraphai-secret"}
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_scrapegraphai_accepts_non_failed_status_with_result() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_MARKDOWNIFY_URL).respond(
            json={
                "request_id": "request-1",
                "status": "queued",
                "result": _MARKDOWN,
                "error": "",
            }
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapeGraphAIFetchProvider(
                ProviderSecrets(
                    {"SCRAPEGRAPHAI_API_KEY": "scrapegraphai-secret"}
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="ScrapeGraphAI",
        content=_MARKDOWN,
        source_provider="scrapegraphai",
        metadata={"request_id": "request-1"},
    )


async def test_scrapegraphai_maps_http_errors() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_MARKDOWNIFY_URL).respond(
            401,
            json={"message": "bad key"},
        )
        async with httpx.AsyncClient() as client:
            provider = ScrapeGraphAIFetchProvider(
                ProviderSecrets(
                    {"SCRAPEGRAPHAI_API_KEY": "scrapegraphai-secret"}
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_scrapegraphai_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapegraphai_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SCRAPEGRAPHAI_API_KEY": "scrapegraphai-secret"})
    ) == ["scrapegraphai"]


async def test_unified_dispatcher_uses_scrapegraphai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(scrapegraphai_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_MARKDOWNIFY_URL).respond(
            json={
                "request_id": "request-1",
                "status": "completed",
                "website_url": _TARGET_URL,
                "result": _MARKDOWN,
                "error": "",
            }
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets(
                    {"SCRAPEGRAPHAI_API_KEY": "scrapegraphai-secret"}
                ),
                client,
            )
            result = await unified.fetch_url(
                _TARGET_URL,
                provider="scrapegraphai",
            )

    assert unified.active_names == ["scrapegraphai"]
    assert result.source_provider == "scrapegraphai"
