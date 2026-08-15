"""Cachelib-backed storage shared by every cached omnifetch surface."""

from __future__ import annotations

from omnifetch.cache.backend import (
    build_cache_backend,
    CacheBackend,
    CachelibBackend,
)

__all__ = ["CacheBackend", "CachelibBackend", "build_cache_backend"]
