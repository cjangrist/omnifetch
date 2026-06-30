"""Tests for the Kimi fetch provider."""

from __future__ import annotations

import importlib
import json
import re
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.kimi as kimi_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.kimi import KimiFetchProvider
from omnifetch.fetch.providers.kimi_proxy import (
    build_kimi_fetch_headers,
    proxy_post_via_scrapfly,
    ScrapflyPostRequest,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_SCRAPFLY_URL = "https://api.scrapfly.io/scrape"
_KIMI_FETCH_URL = "https://api.kimi.com/coding/v1/fetch"
_TARGET_URL = "https://example.test/article"


def _json_request(request: httpx.Request) -> dict[str, str]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, str], payload)


def _scrapfly_payload(
    status_code: int,
    content: str,
    response_headers: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return a Scrapfly proxy response body."""
    return {
        "result": {
            "status_code": status_code,
            "content": content,
            "response_headers": response_headers or {},
        }
    }


def _kimi_payload(
    markdown: str,
    url: str | None = "https://canonical.example/article",
    title: str | None = "Canonical Title",
) -> str:
    """Return a serialized Kimi fetch response body."""
    return json.dumps({"url": url, "markdown": markdown, "title": title})


def test_build_kimi_fetch_headers_matches_cli_identity() -> None:
    headers = build_kimi_fetch_headers("kimi-secret")
    second_headers = build_kimi_fetch_headers("kimi-secret")

    assert headers["User-Agent"] == "KimiCLI/1.37.0"
    assert headers["Authorization"] == "Bearer kimi-secret"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"
    assert re.fullmatch(
        r"fetch-[0-9a-f]{12}",
        headers["X-Msh-Tool-Call-Id"],
    )
    assert headers["X-Msh-Tool-Call-Id"] != second_headers["X-Msh-Tool-Call-Id"]
    assert headers["X-Msh-Platform"] == "kimi_cli"
    assert headers["X-Msh-Version"] == "1.37.0"
    assert headers["X-Msh-Device-Name"] == "device-01"
    assert headers["X-Msh-Device-Model"] == "Linux 6.17.0-1009-gcp x86_64"
    assert (
        headers["X-Msh-Os-Version"]
        == "#9-Ubuntu SMP Fri Mar  6 21:21:14 UTC 2026"
    )
    assert headers["X-Msh-Device-Id"] == "babf43cbff8d4c789b8a8fabc85b0490"


async def test_kimi_fetches_markdown_through_scrapfly() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Content-Type"] == "application/json"
            assert request.url.params["key"] == "scrapfly-secret"
            assert request.url.params["url"] == _KIMI_FETCH_URL
            assert request.url.params["method"] == "POST"
            assert request.url.params["country"] == "us"
            assert (
                request.url.params["headers[Authorization]"]
                == "Bearer kimi-secret"
            )
            assert (
                request.url.params["headers[Content-Type]"]
                == "application/json"
            )
            assert request.url.params["headers[Accept]"] == "application/json"
            assert request.url.params["headers[User-Agent]"] == "KimiCLI/1.37.0"
            assert re.fullmatch(
                r"fetch-[0-9a-f]{12}",
                request.url.params["headers[X-Msh-Tool-Call-Id]"],
            )
            assert _json_request(request) == {"url": _TARGET_URL}
            return httpx.Response(
                200,
                json=_scrapfly_payload(
                    200,
                    _kimi_payload("  # Kimi Article\n\nBody  "),
                    {"x-upstream": "ok"},
                ),
                request=request,
            )

        router.post(_SCRAPFLY_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = KimiFetchProvider(
                ProviderSecrets(
                    {
                        "KIMI_API_KEY": "kimi-secret",
                        "SCRAPFLY_API_KEY": "scrapfly-secret",
                    }
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="Canonical Title",
        content="# Kimi Article\n\nBody",
        source_provider="kimi",
    )


async def test_kimi_uses_markdown_title_fallback() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            200,
            json=_scrapfly_payload(
                200,
                _kimi_payload("# Fallback Title\n\nBody", title=""),
            ),
        )
        async with httpx.AsyncClient() as client:
            provider = KimiFetchProvider(
                ProviderSecrets(
                    {
                        "KIMI_API_KEY": "kimi-secret",
                        "SCRAPFLY_API_KEY": "scrapfly-secret",
                    }
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.title == "Fallback Title"


async def test_kimi_uses_requested_url_when_response_url_is_missing() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            200,
            json=_scrapfly_payload(
                200,
                _kimi_payload("# Article\n\nBody", url=None),
            ),
        )
        async with httpx.AsyncClient() as client:
            provider = KimiFetchProvider(
                ProviderSecrets(
                    {
                        "KIMI_API_KEY": "kimi-secret",
                        "SCRAPFLY_API_KEY": "scrapfly-secret",
                    }
                ),
                client,
            )
            result = await provider.fetch_url(_TARGET_URL)

    assert result.url == _TARGET_URL


async def test_kimi_rejects_non_success_upstream_status() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            200,
            json=_scrapfly_payload(503, "blocked by upstream"),
        )
        async with httpx.AsyncClient() as client:
            provider = KimiFetchProvider(
                ProviderSecrets(
                    {
                        "KIMI_API_KEY": "kimi-secret",
                        "SCRAPFLY_API_KEY": "scrapfly-secret",
                    }
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == "Kimi fetch HTTP 503: blocked by upstream"


async def test_kimi_rejects_malformed_success_body() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            200,
            json=_scrapfly_payload(200, "not-json"),
        )
        async with httpx.AsyncClient() as client:
            provider = KimiFetchProvider(
                ProviderSecrets(
                    {
                        "KIMI_API_KEY": "kimi-secret",
                        "SCRAPFLY_API_KEY": "scrapfly-secret",
                    }
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert "Failed to fetch URL content" in str(error_info.value)


async def test_kimi_rejects_empty_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            200,
            json=_scrapfly_payload(200, _kimi_payload(" ")),
        )
        async with httpx.AsyncClient() as client:
            provider = KimiFetchProvider(
                ProviderSecrets(
                    {
                        "KIMI_API_KEY": "kimi-secret",
                        "SCRAPFLY_API_KEY": "scrapfly-secret",
                    }
                ),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Kimi fetch returned empty markdown"
    )


@pytest.mark.parametrize(
    "secrets",
    [
        {},
        {"SCRAPFLY_API_KEY": "scrapfly-secret"},
        {"KIMI_API_KEY": "kimi-secret"},
    ],
)
async def test_kimi_requires_both_keys(secrets: dict[str, str]) -> None:
    async with httpx.AsyncClient() as client:
        provider = KimiFetchProvider(ProviderSecrets(secrets), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_TARGET_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for kimi"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"result": None},
            "Scrapfly proxy returned no upstream response (status_code=None)",
        ),
        (
            {"result": {"content": "body"}},
            "Scrapfly proxy returned no upstream response (status_code=None)",
        ),
        (
            {"result": {"status_code": 200}},
            "Scrapfly proxy returned no upstream response (status_code=200)",
        ),
    ],
)
async def test_scrapfly_proxy_rejects_missing_upstream_response(
    payload: dict[str, object],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await proxy_post_via_scrapfly(
                    client,
                    ScrapflyPostRequest(
                        "kimi",
                        _KIMI_FETCH_URL,
                        {"Authorization": "Bearer kimi-secret"},
                        "{}",
                        "scrapfly-secret",
                        60_000,
                    ),
                )

    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == message


async def test_scrapfly_proxy_returns_upstream_response() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            200,
            json=_scrapfly_payload(
                201,
                "created",
                {"content-type": "application/json"},
            ),
        )
        async with httpx.AsyncClient() as client:
            proxied = await proxy_post_via_scrapfly(
                client,
                ScrapflyPostRequest(
                    "kimi",
                    _KIMI_FETCH_URL,
                    {"Authorization": "Bearer kimi-secret"},
                    "{}",
                    "scrapfly-secret",
                    60_000,
                ),
            )

    assert proxied.status == 201
    assert proxied.body == "created"
    assert proxied.headers == {"content-type": "application/json"}


@pytest.mark.parametrize(
    ("status_code", "expected_type", "expected_message"),
    [
        (
            403,
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
        ),
        (
            500,
            ErrorType.PROVIDER_ERROR,
            "kimi API internal error (500): down",
        ),
    ],
)
async def test_scrapfly_proxy_maps_scrapfly_http_errors(
    status_code: int,
    expected_type: ErrorType,
    expected_message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPFLY_URL).respond(
            status_code,
            json={"message": "down"},
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await proxy_post_via_scrapfly(
                    client,
                    ScrapflyPostRequest(
                        "kimi",
                        _KIMI_FETCH_URL,
                        {"Authorization": "Bearer kimi-secret"},
                        "{}",
                        "scrapfly-secret",
                        60_000,
                    ),
                )

    assert error_info.value.error_type is expected_type
    assert str(error_info.value) == expected_message


def test_kimi_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(kimi_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert "kimi" not in get_active_fetch_providers(
        ProviderSecrets({"KIMI_API_KEY": "kimi-secret"})
    )
    assert "kimi" not in get_active_fetch_providers(
        ProviderSecrets({"SCRAPFLY_API_KEY": "scrapfly-secret"})
    )
    assert "kimi" in get_active_fetch_providers(
        ProviderSecrets(
            {
                "KIMI_API_KEY": "kimi-secret",
                "SCRAPFLY_API_KEY": "scrapfly-secret",
            }
        )
    )
