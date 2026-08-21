"""Fetch runtime dependency container."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from omnifetch.cache import CacheBackend
from omnifetch.config import (
    DEFAULT_FETCH_CACHE_TTL_SECONDS,
    DEFAULT_VOLATILE_FETCH_CACHE_TTL_SECONDS,
)
from omnifetch.fetch.engine.race import FetchDispatcher
from omnifetch.schemas import FetchResponse


def _same_url(url: str) -> str:
    """Return the URL unchanged, the default cache identity."""
    return url


@dataclass(frozen=True, slots=True)
class Engine:
    """Shared fetch runtime dependencies owned by the server lifespan.

    The client and cache are explicit so direct construction cannot silently
    bypass configured backend selection. Both are owned unless their matching
    ownership flags are false.

    ``canonicalize_cache_url`` decides which URL spellings share one cache
    entry. It defaults to identity, so a standalone server keeps hashing the
    URL exactly as asked. A composing server that already has a canonical form
    -- one that folds a trailing slash, a default port, or a host's casing --
    injects it here so both layers agree on what "the same page" means instead
    of paying twice for it. It affects the key only; the URL handed to a
    provider is still the one the caller asked for.
    """

    unified: FetchDispatcher
    client: httpx.AsyncClient
    cache: CacheBackend
    fetch_cache_ttl_seconds: int = DEFAULT_FETCH_CACHE_TTL_SECONDS
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
    volatile_fetch_cache_ttl_seconds: int = (
        DEFAULT_VOLATILE_FETCH_CACHE_TTL_SECONDS
    )
    canonicalize_cache_url: Callable[[str], str] = _same_url
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
