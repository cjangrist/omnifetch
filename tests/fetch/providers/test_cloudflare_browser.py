"""Tests for the Cloudflare Browser Rendering fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.cloudflare_browser as cloudflare_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.cloudflare_browser import (
    CloudflareBrowserFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
_FETCH_URL = "https://example.test/app"
_MARKDOWN_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{_ACCOUNT_ID}/browser-rendering/markdown"
)
_REQUIRED_SECRETS = {
    "CLOUDFLARE_ACCOUNT_ID": _ACCOUNT_ID,
    "CLOUDFLARE_EMAIL": "user@example.test",
    "CLOUDFLARE_API_KEY": "global-api-key",
}


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_cloudflare_browser_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Auth-Email"] == "user@example.test"
            assert request.headers["X-Auth-Key"] == "global-api-key"
            assert request.headers["Content-Type"] == "application/json"
            assert _json_request(request) == {
                "url": _FETCH_URL,
                "rejectResourceTypes": ["image", "media", "font"],
            }
            return httpx.Response(
                200,
                json={"success": True, "result": "# Rendered\n\nBody"},
                request=request,
            )

        router.post(_MARKDOWN_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = CloudflareBrowserFetchProvider(
                ProviderSecrets(_REQUIRED_SECRETS),
                client,
            )
            result = await provider.fetch_url(_FETCH_URL)

    assert result == FetchResult(
        url=_FETCH_URL,
        title="Rendered",
        content="# Rendered\n\nBody",
        source_provider="cloudflare_browser",
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "success": False,
                "result": "# Ignored",
                "errors": [
                    {"code": 1001, "message": "render blocked"},
                    {"code": 1002, "message": "timeout"},
                ],
            },
            "Cloudflare Browser Rendering failed: render blocked; timeout",
        ),
        (
            {"success": True, "result": "", "errors": []},
            "Cloudflare Browser Rendering failed: No content returned",
        ),
    ],
)
async def test_cloudflare_browser_rejects_failed_or_empty_results(
    payload: dict[str, object],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_MARKDOWN_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            provider = CloudflareBrowserFetchProvider(
                ProviderSecrets(_REQUIRED_SECRETS),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_FETCH_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == f"Failed to fetch URL content: {message}"


@pytest.mark.parametrize(
    ("missing_secret", "message"),
    [
        (
            "CLOUDFLARE_ACCOUNT_ID",
            "Cloudflare account ID not found for cloudflare_browser",
        ),
        (
            "CLOUDFLARE_EMAIL",
            "API key not found for cloudflare_browser",
        ),
        (
            "CLOUDFLARE_API_KEY",
            "API key not found for cloudflare_browser",
        ),
    ],
)
async def test_cloudflare_browser_requires_all_secrets(
    missing_secret: str,
    message: str,
) -> None:
    secrets = {
        key: value
        for key, value in _REQUIRED_SECRETS.items()
        if key != missing_secret
    }

    async with httpx.AsyncClient() as client:
        provider = CloudflareBrowserFetchProvider(
            ProviderSecrets(secrets),
            client,
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_FETCH_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == message


async def test_cloudflare_browser_rejects_invalid_account_id() -> None:
    secrets = {
        **_REQUIRED_SECRETS,
        "CLOUDFLARE_ACCOUNT_ID": "not-an-account-id",
    }

    async with httpx.AsyncClient() as client:
        provider = CloudflareBrowserFetchProvider(
            ProviderSecrets(secrets),
            client,
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_FETCH_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert (
        str(error_info.value)
        == "Invalid Cloudflare account ID for cloudflare_browser"
    )


async def test_cloudflare_browser_maps_http_errors() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_MARKDOWN_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = CloudflareBrowserFetchProvider(
                ProviderSecrets(_REQUIRED_SECRETS),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_FETCH_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_cloudflare_browser_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(cloudflare_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert (
        get_active_fetch_providers(
            ProviderSecrets(
                {
                    "CLOUDFLARE_ACCOUNT_ID": _ACCOUNT_ID,
                    "CLOUDFLARE_EMAIL": "user@example.test",
                }
            )
        )
        == []
    )
    assert get_active_fetch_providers(ProviderSecrets(_REQUIRED_SECRETS)) == [
        "cloudflare_browser"
    ]
