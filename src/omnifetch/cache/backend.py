"""Async, fail-open adapter over cachelib memory, filesystem, and Redis stores.

Cachelib deliberately exposes a synchronous common interface. Request paths
dispatch every operation to a worker thread so Redis and filesystem I/O cannot
block the event loop. Values and connection details are never logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from typing import Any, IO, Protocol, runtime_checkable

from cachelib import FileSystemCache, RedisCache, SimpleCache
from cachelib.base import BaseCache
from cachelib.serializers import JSONSerializer
from redis import Redis

from omnifetch.logging import get_logger

_LOGGER = get_logger("cache")
_REDIS_URL_REQUIRED = (
    "OMNIFETCH_REDIS_URL is required when OMNIFETCH_CACHE_BACKEND=redis"
)
_DISK_PATH_REQUIRED = (
    "OMNIFETCH_DISK_CACHE_PATH must not be empty when "
    "OMNIFETCH_CACHE_BACKEND=disk"
)
_MAX_ENTRIES_REQUIRED = "cache max entries must be a positive integer"
_READINESS_KEY = "omnifetch:cache:readiness"
_REDIS_TIMEOUT_SECONDS = 5.0


class _EncodedJSON(bytes):
    """JSON bytes prepared once before cachelib stores a value."""


class _CacheJSONSerializer(JSONSerializer):
    """Strict JSON serializer with bounded, application-owned warnings."""

    def _warn(self, error: Exception) -> None:
        """Route decode failures through the configured package logger."""
        _LOGGER.warning("Cache value decode failed (%s)", type(error).__name__)

    def dump(
        self,
        value: Any,
        f: IO[bytes],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Write strict JSON to a filesystem cache stream."""
        f.write(self.dumps(value))

    def load(
        self,
        f: IO[bytes],
        *args: Any,
        **kwargs: Any,
    ) -> object:
        """Read JSON from a filesystem cache stream."""
        return self.loads(f.read())

    def dumps(
        self,
        value: object,
        *args: object,
        **kwargs: object,
    ) -> bytes:
        """Encode one value once and reject non-portable JSON."""
        if isinstance(value, _EncodedJSON):
            return bytes(value)
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    def loads(
        self,
        bvalue: str | bytes | bytearray | None,
    ) -> object:
        """Return None for a missing, corrupt, or incompatible value."""
        if bvalue is None:
            return None
        try:
            return json.loads(bvalue)
        except (TypeError, ValueError) as error:
            self._warn(error)
            return None


class _JSONSimpleCache(SimpleCache):
    """Thread-safe memory cache with strict JSON and a hard entry cap."""

    serializer: Any = _CacheJSONSerializer()

    def _over_threshold(self) -> bool:
        """Prune before an insertion would exceed the configured maximum."""
        return len(self._cache) >= self._threshold


class _JSONFileSystemCache(FileSystemCache):
    """Filesystem cache with JSON metadata and a hard entry cap."""

    serializer: Any = _CacheJSONSerializer()

    def _over_threshold(self) -> bool:
        """Prune before an insertion would exceed the configured maximum."""
        return self._threshold != 0 and self._file_count >= self._threshold

    def delete(self, key: str, mgmt_element: bool = False) -> bool:
        """Raise when cachelib reports a filesystem deletion failure."""
        deleted = super().delete(key, mgmt_element=mgmt_element)
        if not deleted:
            raise OSError("filesystem cache deletion failed")
        return True


class _JSONRedisCache(RedisCache):
    """Redis cache with strict, portable JSON values."""

    serializer: Any = _CacheJSONSerializer()


@runtime_checkable
class CacheBackend(Protocol):
    """Async cache operations used by the fetch runtime."""

    async def get(self, key: str) -> object | None:
        """Return a cached value or None for a miss/backend failure."""

    async def set(self, key: str, value: object, ttl_seconds: int) -> bool:
        """Store a value for a positive TTL and report whether it succeeded."""

    async def delete(self, key: str) -> bool:
        """Delete a key, returning false only for a backend failure."""

    async def is_ready(self) -> bool:
        """Return whether the backend currently responds."""

    async def close(self) -> None:
        """Release backend resources without raising."""


class CachelibBackend:
    """Fail-open async wrapper around one cachelib backend."""

    def __init__(
        self,
        cache: BaseCache,
        *,
        readiness_check: Callable[[], object] | None = None,
        close_callback: Callable[[], object] | None = None,
        operation_lock: threading.Lock | None = None,
    ) -> None:
        """Initialize one cachelib store and its optional lifecycle hooks."""
        self._cache = cache
        self._readiness_check = (
            partial(cache.get, _READINESS_KEY)
            if readiness_check is None
            else readiness_check
        )
        self._close_callback = close_callback
        self._operation_lock = operation_lock

    async def get(self, key: str) -> object | None:
        """Return a value or degrade backend/serialization errors to a miss."""
        try:
            return await asyncio.to_thread(
                self._run_operation, partial(self._cache.get, key)
            )
        except Exception as error:
            _LOGGER.warning(
                "Cache read failed for key %s (%s)",
                _key_digest(key),
                type(error).__name__,
            )
            return None

    async def set(self, key: str, value: object, ttl_seconds: int) -> bool:
        """Store a value without allowing backend errors to fail a request."""
        if ttl_seconds < 1:
            _LOGGER.warning(
                "Cache write skipped for key %s (invalid TTL)",
                _key_digest(key),
            )
            return False
        if value is None:
            _LOGGER.warning(
                "Cache write skipped for key %s (None value)",
                _key_digest(key),
            )
            return False
        try:
            stored = await asyncio.to_thread(
                self._run_operation,
                partial(self._set_encoded, key, value, ttl_seconds),
            )
        except Exception as error:
            _LOGGER.warning(
                "Cache write failed for key %s (%s)",
                _key_digest(key),
                type(error).__name__,
            )
            return False
        return bool(stored)

    async def delete(self, key: str) -> bool:
        """Idempotently delete a key without allowing errors to escape."""
        try:
            await asyncio.to_thread(
                self._run_operation, partial(self._cache.delete, key)
            )
        except Exception as error:
            _LOGGER.warning(
                "Cache delete failed for key %s (%s)",
                _key_digest(key),
                type(error).__name__,
            )
            return False
        return True

    async def is_ready(self) -> bool:
        """Probe the backend, degrading probe errors to not-ready."""
        try:
            result = await asyncio.to_thread(
                self._run_operation, self._readiness_check
            )
        except Exception as error:
            _LOGGER.warning(
                "Cache readiness check failed (%s)", type(error).__name__
            )
            return False
        return result is not False

    async def close(self) -> None:
        """Close an owned client, swallowing shutdown errors."""
        if self._close_callback is None:
            return
        try:
            await asyncio.to_thread(self._close_callback)
        except Exception as error:
            _LOGGER.warning("Cache close failed (%s)", type(error).__name__)

    def _run_operation(self, operation: Callable[[], object]) -> object:
        """Run one backend operation under its optional instance lock."""
        if self._operation_lock is None:
            return operation()
        with self._operation_lock:
            return operation()

    def _set_encoded(
        self,
        key: str,
        value: object,
        ttl_seconds: int,
    ) -> object:
        """Encode once in the worker thread, then store the prepared bytes."""
        encoded = _EncodedJSON(_CacheJSONSerializer().dumps(value))
        return self._cache.set(key, encoded, timeout=ttl_seconds)


def _key_digest(key: str) -> str:
    """Return a bounded, irreversible reference for a cache key."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _filesystem_readiness(cache: _JSONFileSystemCache) -> bool:
    """Prove the filesystem backend can write, read, and delete a probe."""
    value = {"ready": True}
    stored = cache.set(_READINESS_KEY, value, timeout=1, mgmt_element=True)
    if not stored:
        return False
    try:
        return bool(cache.get(_READINESS_KEY) == value)
    finally:
        cache.delete(_READINESS_KEY, mgmt_element=True)


def _redis_readiness(client: Redis) -> bool:
    """Require Redis PING to return its exact success value."""
    return client.ping() is True


def _build_redis_cache(redis_url: str) -> CachelibBackend:
    """Build cachelib RedisCache from a redis-py URL client."""
    if not redis_url.strip():
        raise ValueError(_REDIS_URL_REQUIRED)
    client: Redis = Redis.from_url(
        redis_url,
        socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        socket_timeout=_REDIS_TIMEOUT_SECONDS,
    )
    try:
        redis_cache = _JSONRedisCache(host=client, key_prefix="")
    except Exception:
        with suppress(Exception):
            client.close()
        raise
    return CachelibBackend(
        redis_cache,
        readiness_check=partial(_redis_readiness, client),
        close_callback=client.close,
    )


def build_cache_backend(
    backend: str,
    *,
    disk_path: str,
    redis_url: str,
    max_entries: int,
) -> CacheBackend:
    """Build the selected cachelib backend behind the async protocol."""
    if max_entries < 1:
        raise ValueError(_MAX_ENTRIES_REQUIRED)
    if backend == "memory":
        return CachelibBackend(_JSONSimpleCache(threshold=max_entries))
    if backend == "disk":
        if not disk_path.strip():
            raise ValueError(_DISK_PATH_REQUIRED)
        filesystem_cache = _JSONFileSystemCache(
            cache_dir=disk_path,
            threshold=max_entries,
        )
        return CachelibBackend(
            filesystem_cache,
            readiness_check=partial(_filesystem_readiness, filesystem_cache),
            operation_lock=threading.Lock(),
        )
    if backend == "redis":
        return _build_redis_cache(redis_url)
    raise ValueError(f"Unsupported cache backend: {backend}")
