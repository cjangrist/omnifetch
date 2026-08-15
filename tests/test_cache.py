"""Cachelib backend selection, serialization, failures, and lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import call, MagicMock

import cachelib.file as cachelib_file
import cachelib.simple as cachelib_simple
import pytest
from cachelib import RedisCache
from cachelib.base import BaseCache
from redis import Redis

import omnifetch.cache.backend as cache_module
from omnifetch.cache import build_cache_backend, CacheBackend, CachelibBackend


class _BackendKwargs(TypedDict):
    """Typed cache-factory keyword arguments used by tests."""

    disk_path: str
    redis_url: str
    max_entries: int


def _backend_kwargs() -> _BackendKwargs:
    """Return neutral factory arguments for selection/error tests."""
    return {
        "disk_path": ".cache/test",
        "redis_url": "",
        "max_entries": 10,
    }


def _install_redis_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Install Redis client/cache factories and return their mocks."""
    client = MagicMock(spec=Redis)
    client.ping.return_value = True
    from_url = MagicMock(return_value=client)
    cache = MagicMock(spec=RedisCache)
    redis_cache = MagicMock(return_value=cache)
    monkeypatch.setattr(Redis, "from_url", from_url)
    monkeypatch.setattr(cache_module, "_JSONRedisCache", redis_cache)
    return client, from_url, redis_cache


def _filesystem_data_files(cache_path: Path) -> list[Path]:
    """Return filesystem-cache data entries without management files."""
    management_name = hashlib.md5(
        b"__wz_cache_count",
        usedforsecurity=False,
    ).hexdigest()
    return [
        path
        for path in cache_path.iterdir()
        if path.name != management_name
        and not path.name.endswith(".__wz_cache")
    ]


def _filesystem_count(cache_path: Path) -> int:
    """Read cachelib's JSON-encoded filesystem entry count."""
    management_name = hashlib.md5(
        b"__wz_cache_count",
        usedforsecurity=False,
    ).hexdigest()
    count_bytes = (cache_path / management_name).read_bytes()[4:]
    return int(json.loads(count_bytes))


async def test_memory_round_trip_expiry_readiness_and_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cachelib_simple, "time", lambda: clock[0])
    backend = build_cache_backend("memory", **_backend_kwargs())
    value = {"title": "Example", "items": [1, 2]}

    assert isinstance(backend, CacheBackend)
    assert await backend.is_ready() is True
    assert await backend.set("memory-key", value, 2) is True
    assert await backend.get("memory-key") == value
    clock[0] = 1003.0
    assert await backend.get("memory-key") is None
    assert await backend.delete("memory-key") is True
    assert await backend.delete("memory-key") is True
    await backend.close()


async def test_filesystem_round_trip_and_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [2000.0]
    monkeypatch.setattr(cachelib_file, "time", lambda: clock[0])
    backend = build_cache_backend(
        "disk",
        disk_path=str(tmp_path / "cache"),
        redis_url="",
        max_entries=10,
    )
    value = {"url": "https://example.test", "content": "cached"}

    assert await backend.set("disk-key", value, 2) is True
    assert await backend.get("disk-key") == value
    assert await backend.is_ready() is True
    clock[0] = 2003.0
    assert await backend.get("disk-key") is None
    assert await backend.delete("missing-key") is True


async def test_filesystem_readiness_probe_does_not_expire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [3000.0]
    monkeypatch.setattr(cachelib_file, "time", lambda: clock[0])
    backend = build_cache_backend(
        "disk",
        disk_path=str(tmp_path / "cache"),
        redis_url="",
        max_entries=10,
    )
    original_get = cache_module._JSONFileSystemCache.get

    def get_after_clock_jump(
        cache: cache_module._JSONFileSystemCache,
        key: str,
    ) -> Any:
        if key == "omnifetch:cache:readiness":
            clock[0] += 3600
        return original_get(cache, key)

    monkeypatch.setattr(
        cache_module._JSONFileSystemCache,
        "get",
        get_after_clock_jump,
    )

    assert await backend.is_ready() is True


async def test_memory_cache_enforces_a_hard_entry_cap() -> None:
    backend = build_cache_backend(
        "memory",
        disk_path="",
        redis_url="",
        max_entries=2,
    )

    for index in range(4):
        assert await backend.set(f"key-{index}", {"index": index}, 60)

    retained = [await backend.get(f"key-{index}") for index in range(4)]
    assert retained == [None, None, {"index": 2}, {"index": 3}]


async def test_filesystem_metadata_reopens_as_json_and_preserves_cap(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache_path = tmp_path / "cache"
    with caplog.at_level(logging.WARNING):
        first = build_cache_backend(
            "disk",
            disk_path=str(cache_path),
            redis_url="",
            max_entries=2,
        )
        assert await first.set("key-0", {"index": 0}, 60)
        assert await first.set("key-1", {"index": 1}, 60)
        assert _filesystem_count(cache_path) == 2

        reopened = build_cache_backend(
            "disk",
            disk_path=str(cache_path),
            redis_url="",
            max_entries=2,
        )
        assert await reopened.set("key-2", {"index": 2}, 60)
        assert await reopened.set("key-3", {"index": 3}, 60)

    assert _filesystem_count(cache_path) == 2
    assert len(_filesystem_data_files(cache_path)) == 2
    assert caplog.records == []


async def test_filesystem_concurrent_writes_preserve_count_and_cap(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "cache"
    backend = build_cache_backend(
        "disk",
        disk_path=str(cache_path),
        redis_url="",
        max_entries=5,
    )

    results = await asyncio.gather(
        *(
            backend.set(f"key-{index}", {"index": index}, 60)
            for index in range(40)
        )
    )

    assert all(results)
    assert _filesystem_count(cache_path) == 5
    assert len(_filesystem_data_files(cache_path)) == 5


async def test_filesystem_readiness_detects_a_missing_directory(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "cache"
    moved_path = tmp_path / "cache-moved"
    backend = build_cache_backend(
        "disk",
        disk_path=str(cache_path),
        redis_url="",
        max_entries=5,
    )
    assert await backend.is_ready() is True
    cache_path.rename(moved_path)

    try:
        assert await backend.is_ready() is False
    finally:
        moved_path.rename(cache_path)


async def test_filesystem_delete_failure_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = build_cache_backend(
        "disk",
        disk_path=str(tmp_path / "cache"),
        redis_url="",
        max_entries=5,
    )
    assert await backend.set("key", {"value": 1}, 60)
    monkeypatch.setattr(
        cachelib_file.FileSystemCache,
        "delete",
        MagicMock(return_value=False),
    )

    assert await backend.delete("key") is False


async def test_json_serialization_runs_once_in_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = build_cache_backend("memory", **_backend_kwargs())
    event_loop_thread = threading.get_ident()
    serialization_threads: list[int] = []
    original_dumps = json.dumps

    def recording_dumps(*args: Any, **kwargs: Any) -> str:
        serialization_threads.append(threading.get_ident())
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", recording_dumps)

    assert await backend.set("key", {"value": 1}, 60)
    assert len(serialization_threads) == 1
    assert serialization_threads[0] != event_loop_thread


@pytest.mark.parametrize("ttl_seconds", [0, -1])
async def test_write_rejects_non_positive_ttl(ttl_seconds: int) -> None:
    cache = MagicMock(spec=BaseCache)
    backend = CachelibBackend(cache)

    assert await backend.set("safe-key", {"value": 1}, ttl_seconds) is False
    cache.set.assert_not_called()


async def test_write_rejects_non_json_value() -> None:
    cache = MagicMock(spec=BaseCache)
    backend = CachelibBackend(cache)

    assert await backend.set("safe-key", {"value": object()}, 10) is False
    cache.set.assert_not_called()


async def test_write_rejects_none_as_the_cache_miss_sentinel() -> None:
    cache = MagicMock(spec=BaseCache)
    backend = CachelibBackend(cache)

    assert await backend.set("safe-key", None, 10) is False
    cache.set.assert_not_called()


async def test_false_cachelib_write_result_is_preserved() -> None:
    cache = MagicMock(spec=BaseCache)
    cache.set.return_value = False
    backend = CachelibBackend(cache)

    assert await backend.set("safe-key", {"value": 1}, 10) is False


async def test_backend_operations_fail_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = MagicMock(spec=BaseCache)
    cache.get.side_effect = OSError("read failed")
    cache.set.side_effect = OSError("write failed")
    cache.delete.side_effect = OSError("delete failed")
    readiness = MagicMock(side_effect=OSError("ping failed"))
    close = MagicMock(side_effect=OSError("close failed"))
    backend = CachelibBackend(
        cache,
        readiness_check=readiness,
        close_callback=close,
    )

    with caplog.at_level(logging.WARNING, logger="omnifetch.cache"):
        assert await backend.get("omnifetch:fetch:v1:abcdef") is None
        assert await backend.set("omnifetch:fetch:v1:abcdef", {}, 10) is False
        assert await backend.delete("omnifetch:fetch:v1:abcdef") is False
        assert await backend.is_ready() is False
        await backend.close()

    messages = [record.getMessage() for record in caplog.records]
    digest = hashlib.sha256(b"omnifetch:fetch:v1:abcdef").hexdigest()[:12]
    assert len(messages) == 5
    assert all("failed" in message for message in messages)
    assert sum(digest in message for message in messages) == 3
    assert all(
        "omnifetch:fetch:v1:abcdef" not in message for message in messages
    )


async def test_readiness_false_result_is_not_ready() -> None:
    backend = CachelibBackend(
        MagicMock(spec=BaseCache),
        readiness_check=lambda: False,
    )

    assert await backend.is_ready() is False


async def test_redis_uses_url_client_pings_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, from_url, redis_cache = _install_redis_mocks(monkeypatch)
    event_loop_thread = threading.get_ident()
    ping_threads: list[int] = []

    def ping() -> bool:
        ping_threads.append(threading.get_ident())
        return True

    client.ping.side_effect = ping
    backend = build_cache_backend(
        "redis",
        disk_path=".cache/test",
        redis_url="redis://cache.example.test/2",
        max_entries=10,
    )

    from_url.assert_called_once_with(
        "redis://cache.example.test/2",
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
    )
    client.ping.assert_not_called()
    redis_cache.assert_called_once_with(host=client, key_prefix="")
    assert await backend.is_ready() is True
    client.ping.assert_called_once_with()
    assert len(ping_threads) == 1
    assert ping_threads[0] != event_loop_thread
    await backend.close()
    client.close.assert_called_once_with()


async def test_redis_missing_value_is_a_quiet_cache_miss(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = MagicMock(spec=Redis)
    client.ping.return_value = True
    client.get.return_value = None
    monkeypatch.setattr(Redis, "from_url", MagicMock(return_value=client))
    backend = build_cache_backend(
        "redis",
        disk_path=".cache/test",
        redis_url="redis://cache.example.test/2",
        max_entries=10,
    )

    with caplog.at_level(logging.WARNING):
        assert await backend.get("missing-key") is None
    assert caplog.records == []
    await backend.close()


@pytest.mark.parametrize("ping_result", [False, None])
async def test_redis_false_ping_is_not_ready(
    ping_result: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = _install_redis_mocks(monkeypatch)
    client.ping.return_value = ping_result
    backend = build_cache_backend(
        "redis",
        disk_path=".cache/test",
        redis_url="redis://cache.example.test/2",
        max_entries=10,
    )

    assert await backend.is_ready() is False
    await backend.close()
    client.close.assert_called_once_with()


async def test_redis_ping_failure_is_not_ready_and_close_stays_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = _install_redis_mocks(monkeypatch)
    client.ping.side_effect = ConnectionError("redis unavailable")
    client.close.side_effect = OSError("close also failed")
    backend = build_cache_backend(
        "redis",
        disk_path=".cache/test",
        redis_url="redis://cache.example.test/2",
        max_entries=10,
    )

    assert await backend.is_ready() is False
    await backend.close()
    client.close.assert_called_once_with()


def test_redis_cache_construction_failure_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, redis_cache = _install_redis_mocks(monkeypatch)
    redis_cache.side_effect = RuntimeError("cache construction failed")

    with pytest.raises(RuntimeError, match="cache construction failed"):
        build_cache_backend(
            "redis",
            disk_path=".cache/test",
            redis_url="redis://cache.example.test/2",
            max_entries=10,
        )

    client.close.assert_called_once_with()


async def test_real_redis_cachelib_wiring_round_trips_json_and_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, bytes] = {}
    client = MagicMock(spec=Redis)
    client.ping.return_value = True

    def store_value(*, name: str, value: bytes, ex: int) -> bool:
        stored[name] = value
        return True

    def get_value(key: str) -> bytes | None:
        return stored.get(key)

    def delete_value(key: str) -> int:
        return int(stored.pop(key, None) is not None)

    client.set.side_effect = store_value
    client.get.side_effect = get_value
    client.delete.side_effect = delete_value
    monkeypatch.setattr(Redis, "from_url", MagicMock(return_value=client))
    backend = build_cache_backend(
        "redis",
        disk_path="",
        redis_url="redis://cache.example.test/2",
        max_entries=10,
    )
    value = {"title": "Example", "items": [1, 2]}

    assert await backend.set("redis-key", value, 17) is True
    assert await backend.get("redis-key") == value
    assert client.set.call_args == call(
        name="redis-key",
        value=b'{"title":"Example","items":[1,2]}',
        ex=17,
    )
    assert await backend.delete("redis-key") is True
    assert await backend.delete("redis-key") is True


async def test_corrupt_redis_value_uses_the_package_logger(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = MagicMock(spec=Redis)
    client.get.return_value = b"not-json"
    monkeypatch.setattr(Redis, "from_url", MagicMock(return_value=client))
    backend = build_cache_backend(
        "redis",
        disk_path="",
        redis_url="redis://cache.example.test/2",
        max_entries=10,
    )

    with caplog.at_level(logging.WARNING):
        assert await backend.get("corrupt-key") is None

    assert [record.name for record in caplog.records] == ["omnifetch.cache"]
    assert caplog.records[0].getMessage() == (
        "Cache value decode failed (JSONDecodeError)"
    )
