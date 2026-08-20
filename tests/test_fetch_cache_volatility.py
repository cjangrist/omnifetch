"""Homepage-aware fetch cache lifetimes.

A homepage is a rolling index and an article underneath it is immutable, so
the two are stored with different lifetimes. These tests pin which URLs count
as volatile and assert the TTL each one is actually written with.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest

import omnifetch.tools.fetch as fetch_module
from omnifetch.cache import build_cache_backend
from omnifetch.fetch.engine.race import FetchRaceResult
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.tools.fetch import execute_web_fetch, is_volatile_fetch_url

_STABLE_TTL = 864_000
_VOLATILE_TTL = 300


class _FakeDispatcher:
    """Minimal active-provider registry for TTL policy tests."""

    @property
    def active_names(self) -> list[str]:
        return ["tavily"]

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        raise AssertionError("run_fetch_race should be monkeypatched")


def _race(url: str) -> FetchRaceResult:
    """Return one valid provider-race success."""
    return FetchRaceResult(
        requested_url=url,
        total_duration_ms=7,
        provider_used="tavily",
        providers_attempted=("tavily",),
        providers_failed=(),
        result=FetchResult(
            url=url,
            title="Example",
            content="# Example\n\n" + ("useful content " * 30),
            source_provider="tavily",
            metadata={"provider": "tavily"},
        ),
    )


async def _race_ok(
    _dispatcher: object,
    url: str,
    *,
    provider: str | None = None,
    skip_providers: object = (),
) -> FetchRaceResult:
    """Stand in for the billable provider race."""
    return _race(url)


def _engine(
    stable_ttl: int = _STABLE_TTL,
    volatile_ttl: int = _VOLATILE_TTL,
) -> Engine:
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
        fetch_cache_ttl_seconds=stable_ttl,
        volatile_fetch_cache_ttl_seconds=volatile_ttl,
    )


@pytest.fixture
async def ttl_engine() -> AsyncIterator[Engine]:
    """Yield an isolated engine with distinct stable and volatile TTLs."""
    engine = _engine()
    try:
        yield engine
    finally:
        await engine.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "https://cnn.com",
        "https://cnn.com/",
        "http://cnn.com",
        "https://www.bbc.com/",
        "https://example.com:8443",
        "https://example.com?utm_source=x",
        "https://example.com/#section",
    ],
)
def test_homepage_urls_are_volatile(url: str) -> None:
    assert is_volatile_fetch_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://cnn.com/2026/08/20/politics/story",
        "https://cnn.com/politics",
        "https://bbc.com/news",
        "https://example.com/docs/api",
        "https://example.com//",
        "https://example.com/index.html",
    ],
)
def test_deeper_urls_are_not_volatile(url: str) -> None:
    assert is_volatile_fetch_url(url) is False


async def test_homepage_is_written_with_the_volatile_ttl(
    ttl_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "run_fetch_race", _race_ok)
    setter = AsyncMock(return_value=True)
    monkeypatch.setattr(ttl_engine.cache, "set", setter)

    await execute_web_fetch(ttl_engine, "https://cnn.com")

    assert setter.await_args is not None
    assert setter.await_args.args[2] == _VOLATILE_TTL


async def test_article_is_written_with_the_stable_ttl(
    ttl_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "run_fetch_race", _race_ok)
    setter = AsyncMock(return_value=True)
    monkeypatch.setattr(ttl_engine.cache, "set", setter)

    await execute_web_fetch(ttl_engine, "https://cnn.com/2026/08/20/story")

    assert setter.await_args is not None
    assert setter.await_args.args[2] == _STABLE_TTL


async def test_volatile_ttl_never_exceeds_the_stable_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(stable_ttl=60, volatile_ttl=_VOLATILE_TTL)
    monkeypatch.setattr(fetch_module, "run_fetch_race", _race_ok)
    setter = AsyncMock(return_value=True)
    monkeypatch.setattr(engine.cache, "set", setter)

    try:
        await execute_web_fetch(engine, "https://cnn.com")
    finally:
        await engine.aclose()

    assert setter.await_args is not None
    assert setter.await_args.args[2] == 60


async def test_homepage_is_still_cached_and_coalesced(
    ttl_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    races: list[str] = []

    async def counting_race(
        _dispatcher: object,
        url: str,
        *,
        provider: str | None = None,
        skip_providers: object = (),
    ) -> FetchRaceResult:
        races.append(url)
        return _race(url)

    monkeypatch.setattr(fetch_module, "run_fetch_race", counting_race)

    first = await execute_web_fetch(ttl_engine, "https://cnn.com")
    second = await execute_web_fetch(ttl_engine, "https://cnn.com")

    assert races == ["https://cnn.com"]
    assert first.content == second.content
