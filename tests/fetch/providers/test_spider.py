"""Tests for the Spider fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.spider as spider_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.spider import SpiderFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_SCRAPE_URL = "https://api.spider.cloud/scrape"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_spider_fetches_markdown() -> None:
    """Spider maps the first page response into a normalized fetch result."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer spider-secret"
            assert _json_request(request) == {
                "url": _TARGET_URL,
                "return_format": "markdown",
            }
            return httpx.Response(
                200,
                json=[
                    {
                        "url": "https://canonical.example/article",
                        "status": 200,
                        "content": "# Spider\n\nBody",
                    }
                ],
                request=request,
            )

        router.post(_SCRAPE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = SpiderFetchProvider(
                ProviderSecrets({"SPIDER_CLOUD_API_TOKEN": "spider-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Spider",
        content="# Spider\n\nBody",
        source_provider="spider",
        metadata={"status": 200},
    )


async def test_spider_requires_token() -> None:
    """Spider requires its provider-owned API token."""
    async with httpx.AsyncClient() as client:
        provider = SpiderFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for spider"


@pytest.mark.parametrize("page_status", [301, None])
async def test_spider_accepts_content_regardless_of_page_status(
    page_status: int | None,
) -> None:
    """Spider follows TypeScript behavior and trusts non-empty content."""
    page: dict[str, object] = {
        "url": _TARGET_URL,
        "content": "# Spider\n\nBody",
    }
    if page_status is not None:
        page["status"] = page_status

    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(200, json=[page])
        async with httpx.AsyncClient() as client:
            provider = SpiderFetchProvider(
                ProviderSecrets({"SPIDER_CLOUD_API_TOKEN": "spider-secret"}),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url=_TARGET_URL,
        title="Spider",
        content="# Spider\n\nBody",
        source_provider="spider",
        metadata={"status": page_status},
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            [],
            "Failed to fetch URL content: Spider returned empty response",
        ),
        (
            [
                {
                    "url": _TARGET_URL,
                    "status": 200,
                    "content": "# Spider",
                    "error": "crawl blocked",
                }
            ],
            "Failed to fetch URL content: Spider scrape error: crawl blocked",
        ),
        (
            [{"url": _TARGET_URL, "status": 200, "content": ""}],
            "Failed to fetch URL content: Spider returned empty content",
        ),
    ],
)
async def test_spider_rejects_empty_or_failed_results(
    payload: list[dict[str, object]],
    message: str,
) -> None:
    """Spider rejects empty arrays and unusable first-page payloads."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = SpiderFetchProvider(
                ProviderSecrets({"SPIDER_CLOUD_API_TOKEN": "spider-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


@pytest.mark.parametrize(
    ("status_code", "error_type", "message"),
    [
        (401, ErrorType.API_ERROR, "Invalid API key"),
        (
            403,
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
        ),
        (
            404,
            ErrorType.API_ERROR,
            "spider endpoint not found (404): missing",
        ),
        (429, ErrorType.RATE_LIMIT, "Rate limit exceeded for spider"),
        (
            500,
            ErrorType.PROVIDER_ERROR,
            "spider API internal error (500): down",
        ),
    ],
)
async def test_spider_maps_http_errors(
    status_code: int,
    error_type: ErrorType,
    message: str,
) -> None:
    """Spider HTTP statuses use the shared HTTP taxonomy."""
    payload_message = "missing" if status_code == 404 else "down"
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            status_code,
            json={"message": payload_message},
        )
        async with httpx.AsyncClient() as client:
            provider = SpiderFetchProvider(
                ProviderSecrets({"SPIDER_CLOUD_API_TOKEN": "spider-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is error_type
    assert str(error_info.value) == message


def test_spider_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spider self-registers and follows exact token availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(spider_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SPIDER_CLOUD_API_TOKEN": "spider-secret"})
    ) == ["spider"]
