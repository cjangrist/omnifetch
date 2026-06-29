"""Tests for the Supadata fetch provider."""

from __future__ import annotations

import importlib

import httpx
import pytest
import respx

import omnifetch.fetch.providers.supadata as supadata_module
import omnifetch.fetch.providers.tavily as tavily_module
from omnifetch.fetch.engine.race import run_fetch_race
from omnifetch.fetch.providers import base, get_active_fetch_providers
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.providers.supadata import (
    _poll_job,
    _SupadataRequest,
    SupadataFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_TRANSCRIPT_URL = "https://api.supadata.ai/v1/transcript"
_POLL_URL = "https://api.supadata.ai/v1/transcript/job-1"
_YOUTUBE_URL = "https://www.youtube.com/watch?v=abc123"
_MUSIC_YOUTUBE_URL = "https://music.youtube.com/watch?v=music123"


async def test_supadata_fetches_transcript() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-api-key"] == "supadata-secret"
            assert request.url.params["url"] == _YOUTUBE_URL
            assert request.url.params["text"] == "true"
            assert request.url.params["mode"] == "auto"
            assert request.url.params["lang"] == "en"
            return httpx.Response(
                200,
                json={"content": "Transcript body"},
                request=request,
            )

        router.get(_TRANSCRIPT_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            provider = SupadataFetchProvider(
                ProviderSecrets({"SUPADATA_API_KEY": "supadata-secret"}),
                client,
            )
            result = await provider.fetch_url(_YOUTUBE_URL)

    assert result == FetchResult(
        url=_YOUTUBE_URL,
        title="YouTube Transcript: abc123",
        content="# YouTube Video Transcript\n\nTranscript body",
        source_provider="supadata",
    )


async def test_supadata_polls_async_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supadata_module, "_POLL_INTERVAL_S", 0.0)
    poll_payloads = [
        {"status": "queued"},
        {"status": "completed", "content": "Async transcript"},
    ]

    with respx.mock(assert_all_called=True) as router:
        router.get(_TRANSCRIPT_URL).respond(202, json={"jobId": "job-1"})

        def poll_handler(request: httpx.Request) -> httpx.Response:
            payload = poll_payloads.pop(0)
            return httpx.Response(200, json=payload, request=request)

        router.get(_POLL_URL).mock(side_effect=poll_handler)
        async with httpx.AsyncClient() as client:
            provider = SupadataFetchProvider(
                ProviderSecrets({"SUPADATA_API_KEY": "supadata-secret"}),
                client,
            )
            result = await provider.fetch_url(_YOUTUBE_URL)

    assert poll_payloads == []
    assert result.content == "# YouTube Video Transcript\n\nAsync transcript"


@pytest.mark.parametrize(
    ("poll_payload", "message"),
    [
        (
            {"status": "failed", "error": "no captions"},
            "Failed to fetch YouTube transcript: "
            "Supadata transcript job failed: no captions",
        ),
        (
            {"status": "completed"},
            "Failed to fetch YouTube transcript: "
            "Supadata transcript job completed without content",
        ),
    ],
)
async def test_supadata_rejects_failed_async_job(
    poll_payload: dict[str, str],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_TRANSCRIPT_URL).respond(202, json={"jobId": "job-1"})
        router.get(_POLL_URL).respond(200, json=poll_payload)
        async with httpx.AsyncClient() as client:
            provider = SupadataFetchProvider(
                ProviderSecrets({"SUPADATA_API_KEY": "supadata-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_YOUTUBE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_supadata_rejects_empty_immediate_transcript() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(_TRANSCRIPT_URL).respond(200, json={"content": ""})
        async with httpx.AsyncClient() as client:
            provider = SupadataFetchProvider(
                ProviderSecrets({"SUPADATA_API_KEY": "supadata-secret"}),
                client,
            )
            with pytest.raises(ProviderError) as error_info:
                await provider.fetch_url(_YOUTUBE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == (
        "Failed to fetch YouTube transcript: "
        "Supadata returned no transcript for this video"
    )


async def test_supadata_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = SupadataFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_YOUTUBE_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for supadata"


async def test_supadata_rejects_non_youtube_url() -> None:
    async with httpx.AsyncClient() as client:
        provider = SupadataFetchProvider(
            ProviderSecrets({"SUPADATA_API_KEY": "supadata-secret"}),
            client,
        )
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url("https://example.test/article")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert (
        str(error_info.value)
        == "Not a YouTube URL: https://example.test/article"
    )


async def test_supadata_poll_timeout() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(TimeoutError, match="timed out"):
            await _poll_job(
                _SupadataRequest(
                    "supadata",
                    client,
                    "supadata-secret",
                    "https://api.supadata.ai/v1",
                    0,
                ),
                "job-1",
            )


async def test_youtube_breaker_routes_to_supadata_before_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(supadata_module)
    importlib.reload(tavily_module)

    with respx.mock(assert_all_called=True) as router:
        router.get(_TRANSCRIPT_URL).respond(
            200,
            json={"content": "Breaker transcript"},
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets(
                    {
                        "SUPADATA_API_KEY": "supadata-secret",
                        "TAVILY_API_KEY": "tavily-secret",
                    }
                ),
                client,
            )
            result = await run_fetch_race(unified, _MUSIC_YOUTUBE_URL)

    assert result.provider_used == "supadata"
    assert result.providers_attempted == ("supadata",)
    assert result.result.title == "YouTube Transcript: music123"
    assert result.result.source_provider == "supadata"


def test_supadata_registers_and_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(supadata_module)

    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"SUPADATA_API_KEY": "supadata-secret"})
    ) == ["supadata"]
