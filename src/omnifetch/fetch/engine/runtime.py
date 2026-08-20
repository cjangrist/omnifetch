"""Fetch runtime dependency container."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from omnifetch.cache import CacheBackend
from omnifetch.config import (
    DEFAULT_FETCH_CACHE_TTL_SECONDS,
    DEFAULT_VOLATILE_FETCH_CACHE_TTL_SECONDS,
)
from omnifetch.fetch.engine.race import FetchDispatcher
from omnifetch.schemas import FetchResponse


@dataclass(frozen=True, slots=True)
class Engine:
    """Shared fetch runtime dependencies owned by the server lifespan.

    The client and cache are explicit so direct construction cannot silently
    bypass configured backend selection. Both are owned unless their matching
    ownership flags are false.
    """

    unified: FetchDispatcher
    client: httpx.AsyncClient
    cache: CacheBackend
    fetch_cache_ttl_seconds: int = DEFAULT_FETCH_CACHE_TTL_SECONDS
    volatile_fetch_cache_ttl_seconds: int = (
        DEFAULT_VOLATILE_FETCH_CACHE_TTL_SECONDS
    )
    fetch_flights: dict[
        str,
        asyncio.Future[FetchResponse | None],
    ] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
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
