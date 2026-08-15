"""Successful fetch caching, key identity, and miss coalescing tests."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import omnifetch.tools.fetch as fetch_module
from omnifetch.cache import build_cache_backend, CacheBackend
from omnifetch.fetch.engine.race import FetchRaceResult
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.tools.fetch import execute_web_fetch


class _FakeDispatcher:
    """Minimal active-provider registry for cache orchestration tests."""

    def __init__(self, active_names: list[str]) -> None:
        self._active_names = active_names

    @property
    def active_names(self) -> list[str]:
        return self._active_names

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        raise AssertionError("run_fetch_race should be monkeypatched")


def _race(
    url: str,
    provider: str = "tavily",
) -> FetchRaceResult:
    """Return one valid provider-race success for cache tests."""
    return FetchRaceResult(
        requested_url=url,
        total_duration_ms=7,
        provider_used=provider,
        providers_attempted=(provider,),
        providers_failed=(),
        result=FetchResult(
            url=url,
            title="Cached example",
            content="# Cached\n\n" + ("useful content " * 30),
            source_provider=provider,
            metadata={"provider": provider},
        ),
    )


def _memory_engine(
    active_names: list[str] | None = None,
) -> Engine:
    """Build one engine with an isolated real memory cache."""
    return Engine(
        unified=_FakeDispatcher(
            active_names or ["tavily", "firecrawl", "jina"]
        ),
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
    """Return a fake race function that records effective request controls."""

    async def run(
        _dispatcher: _FakeDispatcher,
        url: str,
        *,
        provider: str | None = None,
        skip_providers: Iterable[str] = (),
    ) -> FetchRaceResult:
        calls.append((url, provider, tuple(skip_providers)))
        return _race(url, provider or "tavily")

    return run


async def test_successful_fetch_is_written_then_reused(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[str, str | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        fetch_module,
        "run_fetch_race",
        _recording_race(calls),
    )
    monkeypatch.setattr(
        fetch_module,
        "_cache_hit_duration_ms",
        lambda _start_time: 3,
    )

    with caplog.at_level(logging.DEBUG, logger="omnifetch.tools.fetch"):
        first = await execute_web_fetch(
            cache_engine,
            "  https://example.test/article  ",
        )
        second = await execute_web_fetch(
            cache_engine,
            "https://example.test/article",
        )

    assert first.model_dump(exclude={"total_duration_ms"}) == second.model_dump(
        exclude={"total_duration_ms"}
    )
    assert first.total_duration_ms == 7
    assert second.total_duration_ms == 3
    assert calls == [("https://example.test/article", None, ())]
    assert any("Fetch cache miss" in message for message in caplog.messages)
    assert any(
        "Fetch cache write stored" in message for message in caplog.messages
    )
    assert any("Fetch cache hit" in message for message in caplog.messages)


def test_fetch_cache_keys_are_versioned_hashed_and_control_specific() -> None:
    url = "https://owner:secret@example.test/article"
    default = fetch_module._fetch_cache_key(url, None, [])
    explicit = fetch_module._fetch_cache_key(url, "tavily", [])
    skipped = fetch_module._fetch_cache_key(url, None, ["firecrawl"])

    assert default == fetch_module._fetch_cache_key(url, None, [])
    assert default.startswith("omnifetch:fetch:v1:")
    assert len(default.rsplit(":", maxsplit=1)[-1]) == 64
    assert url not in default
    assert "secret" not in default
    assert len({default, explicit, skipped}) == 3


async def test_provider_and_skip_variants_do_not_collide(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        fetch_module,
        "run_fetch_race",
        _recording_race(calls),
    )
    url = "https://example.test/variants"

    await execute_web_fetch(cache_engine, url)
    await execute_web_fetch(cache_engine, url, provider="tavily")
    await execute_web_fetch(cache_engine, url, skip_providers="firecrawl")
    await execute_web_fetch(cache_engine, url)

    assert calls == [
        (url, None, ()),
        (url, "tavily", ()),
        (url, None, ("firecrawl",)),
    ]


async def test_skip_names_are_canonical_ordered_and_unique(
    cache_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        fetch_module,
        "run_fetch_race",
        _recording_race(calls),
    )
    url = "https://example.test/canonical-skip"

    await execute_web_fetch(
        cache_engine,
        url,
        skip_providers=["JINA", "tavily", "jina"],
    )
    await execute_web_fetch(
        cache_engine,
        url,
        skip_providers=["tavily", "jina"],
    )

    assert calls == [(url, None, ("tavily", "jina"))]


@pytest.mark.parametrize(
    ("provider", "skip_providers", "message"),
    [
        ("tavily", "firecrawl", "mutually exclusive"),
        ("unknown", None, "Unknown explicit provider"),
        (None, ["tavily", "firecrawl", "jina"], "all candidates skipped"),
    ],
)
async def test_invalid_controls_never_consult_cache(
    provider: str | None,
    skip_providers: str | list[str] | None,
    message: str,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.get = AsyncMock()
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher(["tavily", "firecrawl", "jina"]),
        client=client,
        cache=cache,
        owns_cache=False,
    )
    try:
        with pytest.raises(ProviderError, match=message):
            await execute_web_fetch(
                engine,
                "https://example.test/invalid-controls",
                provider=provider,
                skip_providers=skip_providers,
            )
        cache.get.assert_not_awaited()
    finally:
        await engine.aclose()


async def test_no_active_providers_never_consults_cache() -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.get = AsyncMock()
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher([]),
        client=client,
        cache=cache,
        owns_cache=False,
    )
    try:
        with pytest.raises(ProviderError, match="no providers configured"):
            await execute_web_fetch(engine, "https://example.test/no-provider")
        cache.get.assert_not_awaited()
    finally:
        await engine.aclose()


async def test_provider_failure_is_never_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher(["tavily"]),
        client=client,
        cache=cache,
        owns_cache=False,
    )

    async def fail(
        *_args: object,
        **_kwargs: object,
    ) -> FetchRaceResult:
        raise ProviderError(
            ErrorType.PROVIDER_ERROR,
            "provider failed",
            "tavily",
        )

    monkeypatch.setattr(fetch_module, "run_fetch_race", fail)
    try:
        with pytest.raises(ProviderError, match="provider failed"):
            await execute_web_fetch(engine, "https://example.test/failure")
        cache.set.assert_not_awaited()
        assert engine.fetch_flights == {}
    finally:
        await engine.aclose()


async def test_corrupt_entry_and_backend_errors_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.get = AsyncMock(return_value={"url": "incomplete"})
    cache.delete = AsyncMock(side_effect=OSError("delete failed"))
    cache.set = AsyncMock(return_value=False)
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher(["tavily"]),
        client=client,
        cache=cache,
        owns_cache=False,
    )
    calls: list[tuple[str, str | None, tuple[str, ...]]] = []
    monkeypatch.setattr(
        fetch_module,
        "run_fetch_race",
        _recording_race(calls),
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="omnifetch.tools.fetch"):
            response = await execute_web_fetch(
                engine,
                "https://example.test/corrupt",
            )
        assert response.source_provider == "tavily"
        assert len(calls) == 1
        cache.delete.assert_awaited_once()
        cache.set.assert_awaited_once()
        assert any("entry invalid" in message for message in caplog.messages)
        assert any("cleanup failed" in message for message in caplog.messages)
        assert any("write skipped" in message for message in caplog.messages)
    finally:
        await engine.aclose()


async def test_read_and_write_exceptions_fail_open_without_logging_url(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_url = "https://owner:secret@example.test/backend-error"
    cache = MagicMock(spec=CacheBackend)
    cache.get = AsyncMock(side_effect=OSError("read failed"))
    cache.set = AsyncMock(side_effect=OSError("write failed"))
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher(["tavily"]),
        client=client,
        cache=cache,
        owns_cache=False,
    )
    monkeypatch.setattr(
        fetch_module,
        "run_fetch_race",
        _recording_race([]),
    )
    try:
        with caplog.at_level(logging.WARNING, logger="omnifetch.tools.fetch"):
            response = await execute_web_fetch(engine, secret_url)
        assert response.source_provider == "tavily"
        assert any("read failed" in message for message in caplog.messages)
        assert any("write failed" in message for message in caplog.messages)
        assert all(secret_url not in message for message in caplog.messages)
        assert all("secret" not in message for message in caplog.messages)
    finally:
        await engine.aclose()
