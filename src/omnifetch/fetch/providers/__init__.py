"""Fetch provider adapters and unified dispatch helpers."""

from __future__ import annotations

from omnifetch.fetch.providers.base import FetchProvider, get_provider_classes
from omnifetch.fetch.providers.registry import (
    get_active_fetch_providers,
    has_any_fetch_provider,
    import_all_providers,
    UnifiedFetchProvider,
)

__all__ = [
    "FetchProvider",
    "UnifiedFetchProvider",
    "get_active_fetch_providers",
    "get_provider_classes",
    "has_any_fetch_provider",
    "import_all_providers",
]
