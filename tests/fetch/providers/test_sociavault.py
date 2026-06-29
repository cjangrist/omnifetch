"""Tests for the SociaVault fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.sociavault as sociavault_module
import omnifetch.fetch.providers.tavily as tavily_module
from omnifetch.fetch.engine.race import run_fetch_race
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.sociavault import (
    _format_social_content,
    SociaVaultFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, ProviderError

_BASE_URL = "https://api.sociavault.com"
_TAVILY_URL = "https://api.tavily.com/extract"
_LONG_TEXT = "useful social content " * 20


def _response_payload(
    platform: str,
    credits_used: int = 3,
) -> dict[str, object]:
    """Return a successful SociaVault response body."""
    return {
        "success": True,
        "data": {
            "text": _LONG_TEXT,
            "like_count": 12,
            "is_verified": True,
            "tags": ["python", "fetch"],
            "author": {"handle": platform},
            "empty_value": None,
        },
        "creditsUsed": credits_used,
    }


@pytest.mark.parametrize(
    ("url", "platform", "endpoint"),
    [
        (
            "https://old.reddit.com/r/python/comments/abc",
            "reddit",
            "/v1/scrape/reddit/post/comments",
        ),
        (
            "https://www.x.com/user/status/1",
            "twitter",
            "/v1/scrape/twitter/tweet",
        ),
        (
            "https://youtu.be/video123",
            "youtube",
            "/v1/scrape/youtube/video",
        ),
        (
            "https://fb.com/story.php?id=1",
            "facebook",
            "/v1/scrape/facebook/post",
        ),
        (
            "https://www.instagram.com/p/abc",
            "instagram",
            "/v1/scrape/instagram/post-info",
        ),
        (
            "https://www.tiktok.com/@user/video/1",
            "tiktok",
            "/v1/scrape/tiktok/video-info",
        ),
        (
            "https://www.linkedin.com/posts/example",
            "linkedin",
            "/v1/scrape/linkedin/post",
        ),
        (
            "https://www.threads.net/@user/post/1",
            "threads",
            "/v1/scrape/threads/post",
        ),
        (
            "https://www.pinterest.com/pin/1",
            "pinterest",
            "/v1/scrape/pinterest/pin",
        ),
    ],
)
async def test_sociavault_routes_supported_social_urls(
    url: str,
    platform: str,
    endpoint: str,
) -> None:
    """SociaVault maps each supported host family to its endpoint."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-API-Key"] == "sociavault-secret"
            assert request.url.params["url"] == url
            return httpx.Response(
                200,
                json=_response_payload(platform),
                request=request,
            )

        router.get(f"{_BASE_URL}{endpoint}").mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = SociaVaultFetchProvider(
                ProviderSecrets({"SOCIAVAULT_API_KEY": "sociavault-secret"}),
                client,
            )
            result = await provider.fetch_url(url)

    assert result.url == url
    assert result.title == f"{platform} content"
    assert result.source_provider == "sociavault"
    assert result.metadata == {"platform": platform, "credits_used": 3}
    assert result.content.startswith(f"# {platform} content")
    assert "**Text:** useful social content" in result.content
    assert "**Like Count:** 12" in result.content
    assert "**Is Verified:** true" in result.content
    assert "**Tags:** python, fetch" in result.content
    assert '"handle":' in result.content
    assert "empty_value" not in result.content


def test_format_social_content_stringifies_nested_values() -> None:
    """Social content formatting mirrors the TypeScript value rendering."""
    content = _format_social_content(
        "reddit",
        {
            "post_title": "Launch",
            "score": 42,
            "visible": False,
            "items": ["one", 2, True],
            "metadata": {"nested": ["x"]},
            "other": object(),
            "missing": None,
        },
    )

    assert content == (
        "# reddit content\n\n"
        "**Post Title:** Launch\n\n"
        "**Score:** 42\n\n"
        "**Visible:** false\n\n"
        "**Items:** one, 2, true\n\n"
        '**Metadata:** {\n  "nested": [\n    "x"\n  ]\n}\n\n'
        "**Other:** \n"
    )


async def test_sociavault_requires_key() -> None:
    """SociaVault requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = SociaVaultFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url("https://www.instagram.com/p/abc")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for sociavault"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/post",
        "https://snapchat.com/add/example",
        "https://mobile.twitter.com/user/status/1",
        "http://[",
    ],
)
async def test_sociavault_rejects_unsupported_social_urls(url: str) -> None:
    """Unsupported hosts are typed invalid input for waterfall fallthrough."""
    async with httpx.AsyncClient() as client:
        provider = SociaVaultFetchProvider(
            ProviderSecrets({"SOCIAVAULT_API_KEY": "sociavault-secret"}),
            client,
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(url)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value).startswith(
        "SociaVault only supports social media URLs "
    )


async def test_sociavault_rejects_unsuccessful_response() -> None:
    """SociaVault unsuccessful payloads become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_BASE_URL}/v1/scrape/instagram/post-info").respond(
            200,
            json={"success": False, "data": {}},
        )
        async with httpx.AsyncClient() as client:
            provider = SociaVaultFetchProvider(
                ProviderSecrets({"SOCIAVAULT_API_KEY": "sociavault-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://www.instagram.com/p/abc")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch social media content: "
        "SociaVault returned unsuccessful response"
    )


async def test_sociavault_maps_http_errors() -> None:
    """SociaVault HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_BASE_URL}/v1/scrape/instagram/post-info").respond(
            401,
            json={"message": "bad key"},
        )
        async with httpx.AsyncClient() as client:
            provider = SociaVaultFetchProvider(
                ProviderSecrets({"SOCIAVAULT_API_KEY": "sociavault-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url("https://www.instagram.com/p/abc")

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


async def test_social_media_breaker_routes_to_sociavault_before_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Social-media breaker uses SociaVault before the general waterfall."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(sociavault_module)
    importlib.reload(tavily_module)

    with respx.mock(assert_all_called=True) as router:
        router.get(f"{_BASE_URL}/v1/scrape/instagram/post-info").respond(
            200,
            json=_response_payload("instagram"),
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets(
                    {
                        "SOCIAVAULT_API_KEY": "sociavault-secret",
                        "TAVILY_API_KEY": "tavily-secret",
                    }
                ),
                client,
            )
            result = await run_fetch_race(
                unified,
                "https://www.instagram.com/p/abc",
            )

    assert result.provider_used == "sociavault"
    assert result.providers_attempted == ("sociavault",)
    assert result.result.source_provider == "sociavault"


async def test_snapchat_breaker_falls_through_to_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapchat stays breaker-matched but unsupported by SociaVault routes."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(sociavault_module)
    importlib.reload(tavily_module)

    with respx.mock(assert_all_called=True) as router:
        router.post(_TAVILY_URL).respond(
            json={
                "results": [
                    {
                        "url": "https://snapchat.com/add/example",
                        "raw_content": "# Snapchat\n\n" + _LONG_TEXT,
                    }
                ],
                "failed_results": [],
            }
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets(
                    {
                        "SOCIAVAULT_API_KEY": "sociavault-secret",
                        "TAVILY_API_KEY": "tavily-secret",
                    }
                ),
                client,
            )
            result = await run_fetch_race(
                unified,
                "https://snapchat.com/add/example",
            )

    assert result.provider_used == "tavily"
    assert result.providers_attempted == ("sociavault", "tavily")
    assert [failure.provider for failure in result.providers_failed] == [
        "sociavault"
    ]


def test_sociavault_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SociaVault self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(sociavault_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SOCIAVAULT_API_KEY": "sociavault-secret"})
    ) == ["sociavault"]
