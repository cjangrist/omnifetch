"""Tests for the Cloudflare Browser Rendering fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.cloudflare_browser as cloudflare_module
from omnifetch.fetch.providers import (
    base,
    get_active_fetch_providers,
    UnifiedFetchProvider,
)
from omnifetch.fetch.providers.cloudflare_browser import (
    CloudflareBrowserFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_ACCOUNT_ID = "account-1"
_FETCH_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    f"{_ACCOUNT_ID}/browser-rendering/markdown"
)
_SECRETS = {
    "CLOUDFLARE_ACCOUNT_ID": _ACCOUNT_ID,
    "CLOUDFLARE_EMAIL": "user@example.test",
    "CLOUDFLARE_API_KEY": "cloudflare-secret",
}


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


async def test_cloudflare_browser_fetches_rendered_markdown() -> None:
    """Cloudflare Browser maps rendered markdown to a fetch result."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Auth-Email"] == "user@example.test"
            assert request.headers["X-Auth-Key"] == "cloudflare-secret"
            assert request.headers["Content-Type"] == "application/json"
            assert _json_request(request) == {
                "url": "https://example.test/spa",
                "rejectResourceTypes": ["image", "media", "font"],
            }
            return httpx.Response(
                200,
                json={"success": True, "result": "# Rendered\n\nBody"},
                request=request,
            )

        router.post(_FETCH_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = CloudflareBrowserFetchProvider(
                ProviderSecrets(_SECRETS),
                client,
            )
            result = await provider.fetch_url("https://example.test/spa")

    assert result == FetchResult(
        url="https://example.test/spa",
        title="Rendered",
        content="# Rendered\n\nBody",
        source_provider="cloudflare_browser",
    )


@pytest.mark.parametrize(
    "secrets",
    [
        {},
        {
            "CLOUDFLARE_ACCOUNT_ID": _ACCOUNT_ID,
            "CLOUDFLARE_EMAIL": "user@example.test",
        },
    ],
)
async def test_cloudflare_browser_requires_all_keys(
    secrets: dict[str, str],
) -> None:
    """Cloudflare Browser requires account id, email, and API key."""
    async with httpx.AsyncClient() as client:
        provider = CloudflareBrowserFetchProvider(
            ProviderSecrets(secrets), client
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url("https://example.test/spa")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for cloudflare_browser"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "success": False,
                "errors": [{"code": 1000, "message": "render blocked"}],
            },
            "render blocked",
        ),
        ({"success": True, "result": ""}, "No content returned"),
    ],
)
async def test_cloudflare_browser_rejects_failed_response(
    payload: dict[str, object],
    message: str,
) -> None:
    """Failed or empty Browser Rendering responses become provider errors."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = CloudflareBrowserFetchProvider(
                ProviderSecrets(_SECRETS),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/spa")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch URL content: Cloudflare Browser Rendering failed: "
        f"{message}"
    )


async def test_cloudflare_browser_maps_rate_limit_response() -> None:
    """Cloudflare Browser HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(429, json={"message": "slow down"})
        async with httpx.AsyncClient() as client:
            provider = CloudflareBrowserFetchProvider(
                ProviderSecrets(_SECRETS),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://example.test/spa")

    assert error_info.value.error_type is ErrorType.RATE_LIMIT
    assert str(error_info.value) == "Rate limit exceeded for cloudflare_browser"


async def test_unified_dispatcher_uses_cloudflare_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unified dispatcher exposes Cloudflare Browser when keyed."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(cloudflare_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_FETCH_URL).respond(
            200,
            json={"success": True, "result": "# Rendered\n\nBody"},
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(ProviderSecrets(_SECRETS), client)
            result = await unified.fetch_url(
                "https://example.test/spa",
                provider="cloudflare_browser",
            )

    assert unified.active_names == ["cloudflare_browser"]
    assert result.source_provider == "cloudflare_browser"


def test_cloudflare_browser_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloudflare Browser self-registers and follows exact availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(cloudflare_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert (
        get_active_fetch_providers(
            ProviderSecrets(
                {
                    "CLOUDFLARE_ACCOUNT_ID": _ACCOUNT_ID,
                    "CLOUDFLARE_API_KEY": "cloudflare-secret",
                }
            )
        )
        == []
    )
    assert get_active_fetch_providers(ProviderSecrets(_SECRETS)) == [
        "cloudflare_browser"
    ]
