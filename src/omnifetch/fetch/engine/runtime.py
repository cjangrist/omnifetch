"""Fetch runtime dependency container."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from omnifetch.fetch.engine.race import FetchDispatcher


@dataclass(frozen=True, slots=True)
class Engine:
    """Shared fetch runtime dependencies owned by the server lifespan."""

    unified: FetchDispatcher
    client: httpx.AsyncClient
