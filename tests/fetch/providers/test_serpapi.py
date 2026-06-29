"""Tests for the SerpAPI fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.serpapi as serpapi_module
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.serpapi import SerpapiFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_BASE_URL = "https://serpapi.com/search.json"
_YOUTUBE_URL = "https://www.youtube.com/watch?v=video123"


def _success_payload() -> dict[str, object]:
    """Return a successful SerpAPI transcript payload."""
    return {
        "transcript": [
            {"start": 0, "end": 1.2, "snippet": "First segment"},
            {"start": 1.2, "end": 2.4, "snippet": "Second segment"},
        ],
        "search_metadata": {"status": "Success"},
    }


async def test_serpapi_fetches_youtube_transcript() -> None:
    """SerpAPI maps transcript snippets into normalized markdown."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["engine"] == "youtube_video_transcript"
            assert request.url.params["v"] == "video123"
            assert request.url.params["api_key"] == "serpapi-secret"
            return httpx.Response(
                200,
                json=_success_payload(),
                request=request,
            )

        router.get(_BASE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = SerpapiFetchProvider(
                ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"}),
                client,
            )
            result = await provider.fetch_url(_YOUTUBE_URL)

    assert result == FetchResult(
        url=_YOUTUBE_URL,
        title="YouTube Transcript: video123",
        content=("# YouTube Video Transcript\n\nFirst segment Second segment"),
        source_provider="serpapi",
        metadata={"video_id": "video123", "transcript_segments": 2},
    )


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://youtu.be/short123", "short123"),
        ("https://youtube.com/embed/embed123?autoplay=1", "embed123"),
        ("https://youtube.com/shorts/clip123", "clip123"),
        ("https://youtube.com/live/live123", "live123"),
    ],
)
async def test_serpapi_accepts_youtube_url_forms(
    url: str,
    video_id: str,
) -> None:
    """SerpAPI accepts the YouTube URL forms supported by old source."""
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["v"] == video_id
            return httpx.Response(
                200,
                json=_success_payload(),
                request=request,
            )

        router.get(_BASE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = SerpapiFetchProvider(
                ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"}),
                client,
            )
            result = await provider.fetch_url(url)

    assert result.title == f"YouTube Transcript: {video_id}"
    assert result.metadata == {
        "video_id": video_id,
        "transcript_segments": 2,
    }


async def test_serpapi_requires_key() -> None:
    """SerpAPI requires its provider-owned API key."""
    async with httpx.AsyncClient() as client:
        provider = SerpapiFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_YOUTUBE_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for serpapi"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/article",
        "https://youtube.com/channel/channel123",
        "http://[",
    ],
)
async def test_serpapi_rejects_non_youtube_video_urls(url: str) -> None:
    """Non-video URLs are typed invalid input for explicit dispatch."""
    async with httpx.AsyncClient() as client:
        provider = SerpapiFetchProvider(
            ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"}),
            client,
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(url)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == f"Not a YouTube URL: {url[:200]}"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"error": "Invalid video ID"},
            "Failed to fetch YouTube transcript: "
            "SerpAPI error: Invalid video ID",
        ),
        (
            {"transcript": []},
            "Failed to fetch YouTube transcript: "
            "SerpAPI returned no transcript for this video",
        ),
        (
            {},
            "Failed to fetch YouTube transcript: "
            "SerpAPI returned no transcript for this video",
        ),
    ],
)
async def test_serpapi_rejects_error_or_empty_transcript(
    payload: dict[str, object],
    message: str,
) -> None:
    """Upstream error and empty transcript payloads become API errors."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(200, json=payload)
        async with httpx.AsyncClient() as client:
            provider = SerpapiFetchProvider(
                ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_YOUTUBE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_serpapi_maps_http_errors() -> None:
    """SerpAPI HTTP statuses use the shared HTTP taxonomy."""
    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(401, json={"message": "bad key"})
        async with httpx.AsyncClient() as client:
            provider = SerpapiFetchProvider(
                ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_YOUTUBE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_serpapi_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SerpAPI self-registers and follows exact secret availability."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(serpapi_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"})
    ) == ["serpapi"]


async def test_unified_dispatcher_uses_explicit_serpapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SerpAPI is available for explicit provider dispatch when keyed."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(serpapi_module)

    with respx.mock(assert_all_called=True) as router:
        router.get(_BASE_URL).respond(200, json=_success_payload())
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets({"SERPAPI_API_KEY": "serpapi-secret"}),
                client,
            )
            result = await unified.fetch_url(_YOUTUBE_URL, provider="serpapi")

    assert unified.active_names == ["serpapi"]
    assert result.source_provider == "serpapi"
