"""Fetch-cache single-flight and transport-parity tests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport

import omnifetch.tools.fetch as fetch_module
from omnifetch.cache import build_cache_backend, CacheBackend
from omnifetch.config import load_config
from omnifetch.fetch.engine.race import FetchRaceResult
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.schemas import FetchResponse
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
        total_duration_ms=60_000,
        provider_used="tavily",
        providers_attempted=("tavily",),
        providers_failed=(),
        result=FetchResult(
            url=url,
            title="Cached example",
            content="# Cached\n\n" + ("useful content " * 30),
            source_provider="tavily",
            metadata={"provider": {"name": "tavily"}},
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
    expected_claims: int = 1,
) -> tuple[asyncio.Event, Callable[[], int]]:
    """Signal after the expected number of callers joins fetch flights."""
    followers_claimed = asyncio.Event()
    claim_count = 0
    original_claim = fetch_module._claim_fetch_flight

    def claim(
        engine: Engine,
        key: str,
    ) -> tuple[bool, asyncio.Future[FetchResponse | None]]:
        nonlocal claim_count
        result = original_claim(engine, key)
        if not result[0]:
            claim_count += 1
            if claim_count == expected_claims:
                followers_claimed.set()
        return result

    monkeypatch.setattr(fetch_module, "_claim_fetch_flight", claim)
    return followers_claimed, lambda: claim_count


async def _wait_for_event(event: asyncio.Event) -> None:
    """Wait for one synchronization event under a hard failure bound."""
    async with asyncio.timeout(5):
        await event.wait()


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Wait for one in-process condition under a hard failure bound."""
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0)


async def test_concurrent_identical_misses_run_one_provider_race(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    follower_claimed, _claim_count = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        started.set()
        await _wait_for_event(release)
        return _race("https://example.test/concurrent")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    with caplog.at_level(logging.DEBUG, logger="omnifetch.tools.fetch"):
        leader = asyncio.create_task(
            execute_web_fetch(cache_engine, "https://example.test/concurrent")
        )
        await _wait_for_event(started)
        follower = asyncio.create_task(
            execute_web_fetch(cache_engine, "https://example.test/concurrent")
        )
        await _wait_for_event(follower_claimed)
        release.set()
        first, second = await asyncio.gather(leader, follower)

    assert first.model_dump(exclude={"total_duration_ms"}) == second.model_dump(
        exclude={"total_duration_ms"}
    )
    assert first is not second
    assert 0 <= second.total_duration_ms < first.total_duration_ms
    assert first.metadata is not None
    assert second.metadata is not None
    assert first.providers_attempted is not None
    assert second.providers_attempted is not None
    second.metadata["provider"]["name"] = "mutated"
    second.providers_attempted.append("mutated")
    assert first.metadata == {"provider": {"name": "tavily"}}
    assert first.providers_attempted == ["tavily"]
    assert calls == 1
    assert cache_engine.fetch_flights == {}
    assert any("miss coalesced" in message for message in caplog.messages)


async def test_waiters_reuse_response_when_cache_write_does_not_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    follower_claimed, _claim_count = _track_follower_claim(monkeypatch)
    calls = 0
    cache = MagicMock(spec=CacheBackend)
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=False)
    engine = Engine(
        unified=_FakeDispatcher(),
        client=httpx.AsyncClient(),
        cache=cache,
        owns_cache=False,
    )

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        started.set()
        await _wait_for_event(release)
        return _race("https://example.test/non-persisting")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    try:
        leader = asyncio.create_task(
            execute_web_fetch(engine, "https://example.test/non-persisting")
        )
        await _wait_for_event(started)
        waiter = asyncio.create_task(
            execute_web_fetch(engine, "https://example.test/non-persisting")
        )
        await _wait_for_event(follower_claimed)
        release.set()
        first, second = await asyncio.gather(leader, waiter)

        assert first.model_dump(
            exclude={"total_duration_ms"}
        ) == second.model_dump(exclude={"total_duration_ms"})
        assert first is not second
        assert 0 <= second.total_duration_ms < first.total_duration_ms
        assert calls == 1
        assert cache.get.await_count == 3
        cache.set.assert_awaited_once()
        assert engine.fetch_flights == {}
    finally:
        await engine.aclose()


async def test_leader_failure_releases_waiter_to_retry(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    fail_first = asyncio.Event()
    follower_claimed, _claim_count = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await _wait_for_event(fail_first)
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
    await _wait_for_event(first_started)
    waiter = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/retry")
    )
    await _wait_for_event(follower_claimed)
    fail_first.set()

    with pytest.raises(ProviderError, match="leader failed"):
        await leader
    assert (await waiter).source_provider == "tavily"
    assert calls == 2
    assert cache_engine.fetch_flights == {}


async def test_waiters_recoalesce_after_leader_failure(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    fail_first = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()
    followers_recoalesced, claim_count = _track_follower_claim(
        monkeypatch,
        expected_claims=3,
    )
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await _wait_for_event(fail_first)
            raise ProviderError(
                ErrorType.PROVIDER_ERROR,
                "leader failed",
                "tavily",
            )
        second_started.set()
        await _wait_for_event(release_second)
        return _race("https://example.test/recoalesced-retry")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    leader = asyncio.create_task(
        execute_web_fetch(
            cache_engine,
            "https://example.test/recoalesced-retry",
        )
    )
    await _wait_for_event(first_started)
    waiters = [
        asyncio.create_task(
            execute_web_fetch(
                cache_engine,
                "https://example.test/recoalesced-retry",
            )
        )
        for _ in range(2)
    ]
    await _wait_until(lambda: claim_count() == 2)
    fail_first.set()
    await _wait_for_event(second_started)
    await _wait_for_event(followers_recoalesced)
    release_second.set()

    with pytest.raises(ProviderError, match="leader failed"):
        await leader
    responses = await asyncio.gather(*waiters)
    assert all(response.source_provider == "tavily" for response in responses)
    assert responses[0] is not responses[1]
    assert calls == 2
    assert cache_engine.fetch_flights == {}


async def test_leader_cancellation_releases_waiter_to_retry(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_started = asyncio.Event()
    never_release = asyncio.Event()
    follower_claimed, _claim_count = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await _wait_for_event(never_release)
        return _race("https://example.test/cancelled")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    leader = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/cancelled")
    )
    await _wait_for_event(first_started)
    waiter = asyncio.create_task(
        execute_web_fetch(cache_engine, "https://example.test/cancelled")
    )
    await _wait_for_event(follower_claimed)
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
    follower_claimed, _claim_count = _track_follower_claim(monkeypatch)
    calls = 0

    async def run(*_args: object, **_kwargs: object) -> FetchRaceResult:
        nonlocal calls
        calls += 1
        started.set()
        await _wait_for_event(release)
        return _race("https://example.test/waiter-cancelled")

    monkeypatch.setattr(fetch_module, "run_fetch_race", run)
    leader = asyncio.create_task(
        execute_web_fetch(
            cache_engine,
            "https://example.test/waiter-cancelled",
        )
    )
    await _wait_for_event(started)
    waiter = asyncio.create_task(
        execute_web_fetch(
            cache_engine,
            "https://example.test/waiter-cancelled",
        )
    )
    await _wait_for_event(follower_claimed)
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

    response = FetchResponse(
        url="https://example.test/release",
        title="Release",
        content="content",
        source_provider="tavily",
        total_duration_ms=1,
    )
    fetch_module._release_fetch_flight(
        cache_engine,
        key,
        original,
        response,
    )

    assert cache_engine.fetch_flights[key] is replacement
    replacement.set_result(None)
    fetch_module._release_fetch_flight(
        cache_engine,
        key,
        replacement,
        response,
    )
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
