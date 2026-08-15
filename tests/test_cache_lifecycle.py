"""Cache-backed engine and server ownership lifecycle tests."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport

import omnifetch.server as server_module
from omnifetch.cache import build_cache_backend, CacheBackend
from omnifetch.config import load_config
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import FetchResult


class _FakeDispatcher:
    """Minimal dispatcher for engine ownership tests."""

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
        raise AssertionError("ownership tests never dispatch providers")


def test_cache_factory_rejects_invalid_selection_inputs() -> None:
    with pytest.raises(ValueError, match="Unsupported cache backend"):
        build_cache_backend(
            "unknown",
            disk_path=".cache/test",
            redis_url="",
            max_entries=10,
        )
    with pytest.raises(ValueError, match="max entries must be a positive"):
        build_cache_backend(
            "memory",
            disk_path=".cache/test",
            redis_url="",
            max_entries=0,
        )
    with pytest.raises(ValueError, match="DISK_CACHE_PATH must not be empty"):
        build_cache_backend(
            "disk",
            disk_path="  ",
            redis_url="",
            max_entries=10,
        )
    with pytest.raises(ValueError, match="REDIS_URL is required"):
        build_cache_backend(
            "redis",
            disk_path=".cache/test",
            redis_url="  ",
            max_entries=10,
        )


async def test_server_lifespan_closes_shared_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_client = httpx.AsyncClient()
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    cache.is_ready = AsyncMock(return_value=True)
    engine = Engine(
        unified=_FakeDispatcher(["tavily"]),
        client=shared_client,
        cache=cache,
    )
    monkeypatch.setattr(server_module, "build_engine", lambda _: engine)
    server = server_module.build_server(load_config())

    try:
        async with Client(FastMCPTransport(server)) as client:
            await client.list_tools()
            assert shared_client.is_closed is False
        cache.close.assert_awaited_once_with()
        assert shared_client.is_closed is True
    finally:
        if not shared_client.is_closed:
            await shared_client.aclose()


async def test_build_engine_adopts_supplied_http_client() -> None:
    shared_client = httpx.AsyncClient()
    cache = MagicMock()
    cache.__bool__.return_value = False
    try:
        engine = server_module.build_engine(
            load_config(),
            client=shared_client,
            cache=cache,
        )
        assert engine.client is shared_client
        assert engine.cache is cache
    finally:
        await shared_client.aclose()


async def test_build_engine_constructs_http_client_when_omitted() -> None:
    engine = server_module.build_engine(load_config())
    try:
        assert engine.client is not None
        assert engine.client.is_closed is False
        assert engine.fetch_cache_ttl_seconds == 86400
        assert engine.owns_client is True
        assert engine.owns_cache is True
    finally:
        await engine.aclose()


async def test_build_engine_uses_configured_cache_factory_and_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    factory = MagicMock(return_value=cache)
    monkeypatch.setattr(server_module, "build_cache_backend", factory)
    config = load_config(
        cache_backend="disk",
        disk_cache_path="/tmp/omnifetch-test-cache",
        cache_max_entries=123,
        fetch_cache_ttl_seconds=456,
    )

    engine = server_module.build_engine(config)
    try:
        assert engine.cache is cache
        assert engine.fetch_cache_ttl_seconds == 456
        factory.assert_called_once_with(
            "disk",
            disk_path="/tmp/omnifetch-test-cache",
            redis_url="",
            max_entries=123,
        )
    finally:
        await engine.aclose()


def test_build_engine_validates_cache_before_allocating_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_factory = MagicMock(side_effect=ValueError("invalid cache"))
    client_factory = MagicMock()
    monkeypatch.setattr(server_module, "build_cache_backend", cache_factory)
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    with pytest.raises(ValueError, match="invalid cache"):
        server_module.build_engine(load_config())

    client_factory.assert_not_called()


async def test_borrowed_engine_client_survives_server_lifespan() -> None:
    shared_client = httpx.AsyncClient()
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    cache.is_ready = AsyncMock(return_value=True)
    engine = Engine(
        unified=_FakeDispatcher(["tavily"]),
        client=shared_client,
        cache=cache,
    )
    server = server_module.build_server(
        load_config(), engine=engine, own_engine=False
    )

    try:
        async with Client(FastMCPTransport(server)) as client:
            await client.list_tools()
            assert shared_client.is_closed is False
        assert shared_client.is_closed is False
        cache.close.assert_not_awaited()
    finally:
        if not shared_client.is_closed:
            await shared_client.aclose()


async def test_owned_injected_engine_client_closes_on_lifespan_exit() -> None:
    shared_client = httpx.AsyncClient()
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    cache.is_ready = AsyncMock(return_value=True)
    engine = Engine(
        unified=_FakeDispatcher(["tavily"]),
        client=shared_client,
        cache=cache,
    )
    server = server_module.build_server(load_config(), engine=engine)

    try:
        async with Client(FastMCPTransport(server)) as client:
            await client.list_tools()
            assert shared_client.is_closed is False
        cache.close.assert_awaited_once_with()
        assert shared_client.is_closed is True
    finally:
        if not shared_client.is_closed:
            await shared_client.aclose()


async def test_engine_closes_owned_cache_with_a_borrowed_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_client = httpx.AsyncClient()
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    cache.is_ready = AsyncMock(return_value=True)
    monkeypatch.setattr(
        server_module,
        "build_cache_backend",
        MagicMock(return_value=cache),
    )
    engine = server_module.build_engine(load_config(), client=shared_client)
    server = server_module.build_server(load_config(), engine=engine)

    try:
        assert engine.owns_client is False
        assert engine.owns_cache is True
        async with Client(FastMCPTransport(server)) as client:
            await client.list_tools()
        cache.close.assert_awaited_once_with()
        assert shared_client.is_closed is False
    finally:
        await shared_client.aclose()


async def test_engine_closes_owned_client_with_a_borrowed_cache() -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    cache.is_ready = AsyncMock(return_value=True)
    engine = server_module.build_engine(load_config(), cache=cache)
    server = server_module.build_server(load_config(), engine=engine)

    assert engine.owns_client is True
    assert engine.owns_cache is False
    async with Client(FastMCPTransport(server)) as client:
        await client.list_tools()

    cache.close.assert_not_awaited()
    assert engine.client.is_closed is True


async def test_engine_close_is_idempotent_and_always_closes_the_client() -> (
    None
):
    client = httpx.AsyncClient()
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock(side_effect=RuntimeError("cache close failed"))
    engine = Engine(
        unified=_FakeDispatcher([]),
        client=client,
        cache=cache,
    )

    with pytest.raises(RuntimeError, match="cache close failed"):
        await engine.aclose()
    await engine.aclose()

    cache.close.assert_awaited_once_with()
    assert client.is_closed is True


async def test_server_lifespan_rejects_an_unready_owned_cache() -> None:
    client = httpx.AsyncClient()
    cache = MagicMock(spec=CacheBackend)
    cache.is_ready = AsyncMock(return_value=False)
    cache.close = AsyncMock()
    engine = Engine(
        unified=_FakeDispatcher([]),
        client=client,
        cache=cache,
    )
    server = server_module.build_server(load_config(), engine=engine)
    lifespan: Any = server._lifespan

    with pytest.raises(RuntimeError, match="readiness check failed"):
        async with lifespan(server):
            raise AssertionError("unready lifespan must not yield")

    cache.close.assert_awaited_once_with()
    assert client.is_closed is True


def test_build_engine_rolls_back_cache_when_client_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    monkeypatch.setattr(
        server_module,
        "build_cache_backend",
        MagicMock(return_value=cache),
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        MagicMock(side_effect=RuntimeError("client construction failed")),
    )

    with pytest.raises(RuntimeError, match="client construction failed"):
        server_module.build_engine(load_config())

    cache.close.assert_awaited_once_with()


def test_build_engine_does_not_close_borrowed_cache_on_client_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        MagicMock(side_effect=RuntimeError("client construction failed")),
    )

    with pytest.raises(RuntimeError, match="client construction failed"):
        server_module.build_engine(load_config(), cache=cache)

    cache.close.assert_not_awaited()


def test_build_engine_rolls_back_both_resources_when_provider_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    client = httpx.AsyncClient()
    monkeypatch.setattr(
        server_module,
        "build_cache_backend",
        MagicMock(return_value=cache),
    )
    monkeypatch.setattr(httpx, "AsyncClient", MagicMock(return_value=client))
    monkeypatch.setattr(
        server_module,
        "UnifiedFetchProvider",
        MagicMock(side_effect=RuntimeError("provider construction failed")),
    )

    with pytest.raises(RuntimeError, match="provider construction failed"):
        server_module.build_engine(load_config())

    cache.close.assert_awaited_once_with()
    assert client.is_closed is True


async def test_build_server_rolls_back_in_a_running_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher([]),
        client=client,
        cache=cache,
    )
    monkeypatch.setattr(
        server_module,
        "register_tools",
        MagicMock(side_effect=RuntimeError("tool registration failed")),
    )

    with pytest.raises(RuntimeError, match="tool registration failed"):
        server_module.build_server(load_config(), engine=engine)

    cache.close.assert_awaited_once_with()
    assert client.is_closed is True


def test_build_server_rolls_back_when_fastmcp_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher([]),
        client=client,
        cache=cache,
    )
    monkeypatch.setattr(
        server_module,
        "FastMCP",
        MagicMock(side_effect=RuntimeError("server construction failed")),
    )

    with pytest.raises(RuntimeError, match="server construction failed"):
        server_module.build_server(load_config(), engine=engine)

    cache.close.assert_awaited_once_with()
    assert client.is_closed is True


def test_borrowed_resources_survive_registration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock()
    client = httpx.AsyncClient()
    engine = Engine(
        unified=_FakeDispatcher([]),
        client=client,
        cache=cache,
    )
    monkeypatch.setattr(
        server_module,
        "register_tools",
        MagicMock(side_effect=RuntimeError("tool registration failed")),
    )

    with pytest.raises(RuntimeError, match="tool registration failed"):
        server_module.build_server(
            load_config(),
            engine=engine,
            own_engine=False,
        )

    cache.close.assert_not_awaited()
    assert client.is_closed is False
    asyncio.run(client.aclose())


def test_rollback_cleanup_failure_does_not_mask_assembly_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = MagicMock(spec=CacheBackend)
    cache.close = AsyncMock(side_effect=OSError("cache close failed"))
    monkeypatch.setattr(
        server_module,
        "build_cache_backend",
        MagicMock(return_value=cache),
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        MagicMock(side_effect=RuntimeError("client construction failed")),
    )

    with (
        caplog.at_level(logging.WARNING, logger="omnifetch.server"),
        pytest.raises(RuntimeError, match="client construction failed"),
    ):
        server_module.build_engine(load_config())

    assert "Resource rollback failed (OSError)" in caplog.messages


def test_build_server_rejects_unowned_self_built_engine() -> None:
    """own_engine=False with engine=None would leak the built client."""
    with pytest.raises(ValueError, match="own_engine=False requires an engine"):
        server_module.build_server(load_config(), own_engine=False)
