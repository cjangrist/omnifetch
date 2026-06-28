"""Tests for Tavily and Firecrawl fetch providers."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.firecrawl as firecrawl_module
import omnifetch.fetch.providers.tavily as tavily_module
from omnifetch.fetch.providers import (
    base,
    get_active_fetch_providers,
    UnifiedFetchProvider,
)
from omnifetch.fetch.providers.firecrawl import FirecrawlFetchProvider
from omnifetch.fetch.providers.tavily import TavilyFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_NO_TAVILY_CONTENT_MESSAGE = (
    "Failed to fetch URL content: No content returned from Tavily extract"
)


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_tavily_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer tavily-secret"
            assert _json_request(request) == {
                "urls": ["https://example.test/article"],
                "extract_depth": "basic",
                "format": "markdown",
            }
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://canonical.example/article",
                            "raw_content": "# Article\n\nBody",
                        }
                    ],
                    "failed_results": [],
                },
                request=request,
            )

        router.post("https://api.tavily.com/extract").mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = TavilyFetchProvider(
                ProviderSecrets({"TAVILY_API_KEY": "tavily-secret"}),
                client,
            )
            result = await provider.fetch_url("https://example.test/article")

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="Article",
        content="# Article\n\nBody",
        source_provider="tavily",
    )


async def test_tavily_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = TavilyFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for tavily"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "results": [],
                "failed_results": [
                    {"url": "https://example.test/article", "error": "blocked"}
                ],
            },
            "Failed to fetch URL content: Tavily extraction failed: blocked",
        ),
        (
            {"results": [], "failed_results": []},
            _NO_TAVILY_CONTENT_MESSAGE,
        ),
        (
            {
                "results": [
                    {
                        "url": "https://example.test/article",
                        "raw_content": "",
                    }
                ],
                "failed_results": [],
            },
            _NO_TAVILY_CONTENT_MESSAGE,
        ),
    ],
)
async def test_tavily_rejects_empty_or_failed_results(
    payload: dict[str, object],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/extract").respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = TavilyFetchProvider(
                ProviderSecrets({"TAVILY_API_KEY": "tavily-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_firecrawl_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer fire-secret"
            assert _json_request(request) == {
                "url": "https://example.test/article",
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "# Firecrawl\n\nBody",
                        "metadata": {
                            "title": "Firecrawl",
                            "description": "Extracted page",
                            "sourceURL": "https://canonical.example/article",
                            "statusCode": 200,
                        },
                    },
                },
                request=request,
            )

        router.post("https://api.firecrawl.dev/v2/scrape").mock(
            side_effect=handler
        )
        async with httpx.AsyncClient() as client:
            provider = FirecrawlFetchProvider(
                ProviderSecrets({"FIRECRAWL_API_KEY": "fire-secret"}),
                client,
            )
            result = await provider.fetch_url("https://example.test/article")

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="Firecrawl",
        content="# Firecrawl\n\nBody",
        source_provider="firecrawl",
        metadata={"description": "Extracted page", "status_code": 200},
    )


async def test_firecrawl_uses_url_fallback_without_metadata() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.firecrawl.dev/v2/scrape").respond(
            json={
                "success": True,
                "data": {"markdown": "# Firecrawl\n\nBody"},
            }
        )
        async with httpx.AsyncClient() as client:
            provider = FirecrawlFetchProvider(
                ProviderSecrets({"FIRECRAWL_API_KEY": "fire-secret"}),
                client,
            )
            result = await provider.fetch_url("https://example.test/article")

    assert result == FetchResult(
        url="https://example.test/article",
        title="",
        content="# Firecrawl\n\nBody",
        source_provider="firecrawl",
    )


async def test_firecrawl_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = FirecrawlFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for firecrawl"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"success": False, "data": {"markdown": "# Nope"}},
            "Failed to fetch URL content: Firecrawl scrape failed",
        ),
        (
            {"success": True},
            "Failed to fetch URL content: Firecrawl scrape returned no content",
        ),
        (
            {"success": True, "data": {"markdown": ""}},
            "Failed to fetch URL content: Firecrawl scrape returned no content",
        ),
    ],
)
async def test_firecrawl_rejects_failed_or_empty_results(
    payload: dict[str, object],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.firecrawl.dev/v2/scrape").respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = FirecrawlFetchProvider(
                ProviderSecrets({"FIRECRAWL_API_KEY": "fire-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


def test_tavily_and_firecrawl_register_and_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})

    importlib.reload(firecrawl_module)
    importlib.reload(tavily_module)

    tavily_secrets = ProviderSecrets({"TAVILY_API_KEY": "tavily-secret"})
    both_secrets = ProviderSecrets(
        {
            "FIRECRAWL_API_KEY": "fire-secret",
            "TAVILY_API_KEY": "tavily-secret",
        }
    )

    assert get_active_fetch_providers(tavily_secrets) == ["tavily"]
    assert get_active_fetch_providers(both_secrets) == ["firecrawl", "tavily"]


async def test_unified_dispatcher_uses_tavily_and_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})

    importlib.reload(firecrawl_module)
    importlib.reload(tavily_module)

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.firecrawl.dev/v2/scrape").respond(
            json={
                "success": True,
                "data": {"markdown": "# Firecrawl\n\nBody"},
            }
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"FIRECRAWL_API_KEY": "fire-secret"}),
                client,
            )
            result = await unified.fetch_url(
                "https://example.test/article",
                provider="firecrawl",
            )

    assert unified.active_names == ["firecrawl"]
    assert result.source_provider == "firecrawl"
