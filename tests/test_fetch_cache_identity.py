"""Injectable cache-URL canonicalization.

Which URL spellings share one cache entry is the composing server's decision.
These tests pin the default (identity), the effect of an injected
canonicalizer, and that a misbehaving one can never cost a paid fetch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import cast

import httpx
import pytest

import omnifetch.tools.fetch as fetch_module
from omnifetch.cache import build_cache_backend, CacheBackend
from omnifetch.config import load_config
from omnifetch.fetch.engine.race import FetchRaceResult
from omnifetch.fetch.engine.runtime import Engine, same_url
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.server import build_engine
from omnifetch.tools.fetch import execute_web_fetch

RACES: list[str] = []


class _FakeDispatcher:
    """Minimal active-provider registry for cache-identity tests."""

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


async def _counting_race(
    _dispatcher: object,
    url: str,
    *,
    provider: str | None = None,
    skip_providers: object = (),
) -> FetchRaceResult:
    """Record every billable provider race."""
    RACES.append(url)
    return _race(url)


def _drop_trailing_slash(url: str) -> str:
    """Stand in for a composing server's canonical URL form."""
    return url.rstrip("/") or url


def _memory_cache() -> CacheBackend:
    """Return one isolated real memory cache."""
    return build_cache_backend(
        "memory",
        disk_path="",
        redis_url="",
        max_entries=100,
    )


def _default_engine() -> Engine:
    """Build an engine keeping omnifetch's own identity canonicalization."""
    return Engine(
        unified=_FakeDispatcher(),
        client=httpx.AsyncClient(),
        cache=_memory_cache(),
    )


def _engine(canonicalize: Callable[[str], str]) -> Engine:
    """Build an engine with an injected canonicalizer."""
    return Engine(
        unified=_FakeDispatcher(),
        client=httpx.AsyncClient(),
        cache=_memory_cache(),
        canonicalize_cache_url=canonicalize,
    )


@pytest.fixture(autouse=True)
def _reset_races(monkeypatch: pytest.MonkeyPatch) -> None:
    RACES.clear()
    monkeypatch.setattr(fetch_module, "run_fetch_race", _counting_race)


@pytest.fixture
async def plain_engine() -> AsyncIterator[Engine]:
    """Yield an engine using the default identity canonicalization."""
    engine = _default_engine()
    try:
        yield engine
    finally:
        await engine.aclose()


async def test_default_keeps_url_spellings_separate(
    plain_engine: Engine,
) -> None:
    await execute_web_fetch(plain_engine, "https://example.com/x")
    await execute_web_fetch(plain_engine, "https://example.com/x/")

    assert len(RACES) == 2


async def test_injected_canonicalizer_collapses_spellings() -> None:
    engine = _engine(_drop_trailing_slash)
    try:
        first = await execute_web_fetch(engine, "https://example.com/x")
        second = await execute_web_fetch(engine, "https://example.com/x/")
    finally:
        await engine.aclose()

    assert RACES == ["https://example.com/x"]
    assert first.content == second.content


async def test_provider_receives_the_url_as_asked() -> None:
    engine = _engine(_drop_trailing_slash)
    try:
        await execute_web_fetch(engine, "https://example.com/y/")
    finally:
        await engine.aclose()

    assert RACES == ["https://example.com/y/"]


async def test_raising_canonicalizer_falls_back_to_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def explode(url: str) -> str:
        raise ValueError("boom")

    engine = _engine(explode)
    try:
        response = await execute_web_fetch(engine, "https://example.com/z")
        repeated = await execute_web_fetch(engine, "https://example.com/z")
    finally:
        await engine.aclose()

    assert response.source_provider == "tavily"
    assert repeated.content == response.content
    assert RACES == ["https://example.com/z"]
    assert "canonicalization failed (ValueError)" in caplog.text


async def test_empty_canonicalization_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = _engine(lambda url: "")
    try:
        await execute_web_fetch(engine, "https://example.com/a")
        await execute_web_fetch(engine, "https://example.com/b")
    finally:
        await engine.aclose()

    assert RACES == ["https://example.com/a", "https://example.com/b"]
    assert "rejected a empty result" in caplog.text


async def test_non_string_canonicalization_is_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A URL object would reach json.dumps and raise on a paying path."""

    def as_url_object(url: str) -> str:
        return cast(str, httpx.URL(url))

    engine = _engine(as_url_object)
    try:
        await execute_web_fetch(engine, "https://example.com/c")
        await execute_web_fetch(engine, "https://example.com/d")
    finally:
        await engine.aclose()

    assert RACES == ["https://example.com/c", "https://example.com/d"]
    assert "rejected a URL result" in caplog.text


def test_same_url_is_the_exported_default() -> None:
    assert same_url("https://example.com/x/") == "https://example.com/x/"
    assert Engine.__dataclass_fields__["canonicalize_cache_url"].default is (
        same_url
    )


async def test_build_engine_passes_the_canonicalizer_through() -> None:
    client = httpx.AsyncClient()
    try:
        engine = build_engine(
            load_config(),
            client=client,
            cache=_memory_cache(),
            canonicalize_cache_url=_drop_trailing_slash,
        )
        assert engine.canonicalize_cache_url is _drop_trailing_slash
    finally:
        await client.aclose()


async def test_build_engine_defaults_to_identity() -> None:
    client = httpx.AsyncClient()
    try:
        engine = build_engine(
            load_config(),
            client=client,
            cache=_memory_cache(),
        )
        assert engine.canonicalize_cache_url is same_url
    finally:
        await client.aclose()
