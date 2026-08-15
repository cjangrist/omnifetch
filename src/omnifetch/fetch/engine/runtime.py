"""Fetch runtime dependency container."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from omnifetch.cache import build_cache_backend, CacheBackend
from omnifetch.config import (
    DEFAULT_CACHE_MAX_ENTRIES,
    DEFAULT_DISK_CACHE_PATH,
    DEFAULT_FETCH_CACHE_TTL_SECONDS,
)
from omnifetch.fetch.engine.race import FetchDispatcher


def _default_cache() -> CacheBackend:
    """Build the in-memory default used by directly constructed test engines."""
    return build_cache_backend(
        "memory",
        disk_path=DEFAULT_DISK_CACHE_PATH,
        redis_url="",
        max_entries=DEFAULT_CACHE_MAX_ENTRIES,
    )


@dataclass(frozen=True, slots=True)
class Engine:
    """Shared fetch runtime dependencies owned by the server lifespan."""

    unified: FetchDispatcher
    client: httpx.AsyncClient
    cache: CacheBackend = field(default_factory=_default_cache)
    fetch_cache_ttl_seconds: int = DEFAULT_FETCH_CACHE_TTL_SECONDS
    owns_client: bool = True
    owns_cache: bool = True
    _close_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
        compare=False,
    )
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    async def aclose(self) -> None:
        """Idempotently close only the resources this engine owns."""
        async with self._close_lock:
            if self._closed:
                return
            object.__setattr__(self, "_closed", True)
            try:
                if self.owns_cache:
                    await self.cache.close()
            finally:
                if self.owns_client:
                    await self.client.aclose()
