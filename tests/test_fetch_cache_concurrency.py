"""Fetch-cache single-flight and transport-parity tests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport

import omnifetch.tools.fetch as fetch_module
from omnifetch.cache import build_cache_backend
from omnifetch.config import load_config
from omnifetch.fetch.engine.race import FetchRaceResult
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.server import build_server
from omnifetch.tools.fetch import execute_web_fetch


class _FakeDispatcher:
    """Minimal active-provider registry for cache concurrency tests."""

    @property
    def active_names(self) -> list[str]:
        return ["tavily", "firecrawl", "jina"]

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        raise AssertionError("run_fetch_race should be monkeypatched")


def _race(url: str) -> FetchRaceResult:
    """Return one valid provider-race success for concurrency tests."""
    return FetchRaceResult(
        requested_url=url,
        total_duration_ms=7,
        provider_used="tavily",
        providers_attempted=("tavily",),
        providers_failed=(),
        result=FetchResult(
            url=url,
            title="Cached example",
            content="# Cached\n\n" + ("useful content " * 30),
            source_provider="tavily",
            metadata={"provider": "tavily"},
        ),
    )


def _memory_engine() -> Engine:
    """Build one engine with an isolated real memory cache."""
    return Engine(
        unified=_FakeDispatcher(),
        client=httpx.AsyncClient(),
        cache=build_cache_backend(
            "memory",
            disk_path="",
            redis_url="",
            max_entries=100,
        ),
        fetch_cache_ttl_seconds=60,
    )


@pytest.fixture
async def cache_engine() -> AsyncIterator[Engine]:
    """Yield an isolated cache-enabled engine and close it afterward."""
    engine = _memory_engine()
    try:
        yield engine
    finally:
        await engine.aclose()


def _recording_race(
    calls: list[tuple[str, str | None, tuple[str, ...]]],
) -> Callable[..., Awaitable[FetchRaceResult]]:
    """Return a fake race function that records effective controls."""

    async def run(
        _dispatcher: _FakeDispatcher,
        url: str,
        *,
        provider: str | None = None,
        skip_providers: Iterable[str] = (),
    ) -> FetchRaceResult:
        calls.append((url, provider, tuple(skip_providers)))
        return _race(url)

    return run


def _track_follower_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> asyncio.Event:
    """Signal when one caller joins an existing fetch flight."""
    follower_claimed = asyncio.Event()
    original_claim = fetch_module._claim_fetch_flight

    def claim(
        engine: Engine,
        key: str,
    ) -> tuple[bool, asyncio.Future[None]]:
        result = original_claim(engine, key)
        if not result[0]:
            follower_claimed.set()
        return result

    monkeypatch.setattr(fetch_module, "_claim_fetch_flight", claim)
    return follower_claimed


async def test_concurrent_identical_misses_run_one_provider_race(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    follower_claimed = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _race("https://example.test/concurrent")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    with caplog.at_level(logging.DEBUG, logger="omnifetch.tools.fetch"):
        leader = asyncio.create_task(
            execute_web_fetch(cache_engine, "https://example.test/concurrent")
        )
        await started.wait()
        follower = asyncio.create_task(
            execute_web_fetch(cache_engine, "https://example.test/concurrent")
        )
        await follower_claimed.wait()
        release.set()
        first, second = await asyncio.gather(leader, follower)

    assert first == second
    assert calls == 1
    assert cache_engine.fetch_flights == {}
    assert any("miss coalesced" in message for message in caplog.messages)


async def test_leader_failure_releases_waiter_to_retry(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    fail_first = asyncio.Event()
    follower_claimed = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await fail_first.wait()
            raise ProviderError(
                ErrorType.PROVIDER_ERROR,
                "leader failed",
                "tavily",
            )
        return _race("https://example.test/retry")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    leader = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/retry")
    )
    await first_started.wait()
    waiter = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/retry")
    )
    await follower_claimed.wait()
    fail_first.set()

    with pytest.raises(ProviderError, match="leader failed"):
        await leader
    assert (await waiter).source_provider == "tavily"
    assert calls == 2
    assert cache_engine.fetch_flights == {}


async def test_leader_cancellation_releases_waiter_to_retry(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    never_release = asyncio.Event()
    follower_claimed = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await never_release.wait()
        return _race("https://example.test/cancelled")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    leader = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/cancelled")
    )
    await first_started.wait()
    waiter = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/cancelled")
    )
    await follower_claimed.wait()
    leader.cancel()

    with pytest.raises(asyncio.CancelledError):
        await leader
    assert (await waiter).source_provider == "tavily"
    assert calls == 2
    assert cache_engine.fetch_flights == {}


async def test_cancelled_waiter_does_not_cancel_shared_flight(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    follower_claimed = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return _race("https://example.test/waiter-cancelled")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    leader = asyncio.create_task(
        execute_web_fetch(
            cache_engine,
            "https://example.test/waiter-cancelled",
        )
    )
    await started.wait()
    waiter = asyncio.create_task(
        execute_web_fetch(
            cache_engine,
            "https://example.test/waiter-cancelled",
        )
    )
    await follower_claimed.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()

    await leader
    await execute_web_fetch(
        cache_engine,
        "https://example.test/waiter-cancelled",
    )
    assert calls == 1
    assert cache_engine.fetch_flights == {}


async def test_release_ignores_replaced_and_completed_flight(
    cache_engine: Engine,
) -> None:
    key = "omnifetch:fetch:v1:test"
    original = asyncio.get_running_loop().create_future()
    replacement = asyncio.get_running_loop().create_future()
    original.set_result(None)
    cache_engine.fetch_flights[key] = replacement

    fetch_module._release_fetch_flight(cache_engine, key, original)

    assert cache_engine.fetch_flights[key] is replacement
    replacement.set_result(None)
    fetch_module._release_fetch_flight(cache_engine, key, replacement)
    assert cache_engine.fetch_flights == {}


async def test_rest_and_mcp_reuse_the_same_cache_entry(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        fetch_module,
        "run_fetch_race",
        _recording_race(calls),
    )
    server = build_server(
        load_config(transport="http"),
        engine=cache_engine,
        own_engine=False,
    )
    app = server.http_app(transport="http")
    transport = httpx.ASGITransport(app=app)
    url = "https://example.test/transport-parity"

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as rest_client:
        response = await rest_client.post("/web_fetch", json={"url": url})
    assert response.status_code == 200

    async with Client(FastMCPTransport(server)) as mcp_client:
        result = await mcp_client.call_tool("web_fetch", {"url": url})

    assert result.is_error is False
    assert result.data.url == response.json()["url"]
    assert calls == [(url, None, ())]
