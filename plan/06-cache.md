# 06 — Cache layer (`fetch/engine/cache.py`)

> Re-targets Cloudflare KV (`fetch_orchestrator.ts:30-90`) to a **backend-agnostic**
> cache via [`py-key-value`](https://github.com/strawgate/py-key-value)
> (`py-key-value-aio`). Default backend is an **in-process `MemoryStore`** (correct
> + fastest for the single-process stdio MCP target); `RedisStore` (restart-survival
> / cross-replica sharing) and `DiskStore` drop in **via config with zero
> orchestrator changes**.
>
> Source: `fetch_orchestrator.ts:30-90` (`get_fetch_cached`, `set_fetch_cached`,
> `is_valid_cached_fetch`) + the `FetchRaceResult` shape (`:157-165`).
>
> **Design change (vs. the original draft):** replaces a hand-rolled
> `CacheBackend` ABC + `InMemoryTTLCache` with `py-key-value`'s `AsyncKeyValue`
> protocol + stores. We no longer hand-roll TTL/eviction/serialization — the library
> owns all three, and swapping `memory → redis → disk` is a one-line factory change.
> (Per the GEMINI.md review #2: "concrete persistent/shared cache backend" — solved
> generically instead of hard-coding Redis.)

---

## 06.1 What the TS version does
- Key: `hash_key("fetch:", url)` → `"fetch:" + sha256(url)` (`:68,85`).
- TTL: **36 hours** = `129_600` s (`KV_FETCH_TTL_SECONDS`, `:30`).
- **Write** (`set_fetch_cached`, `:82-90`): `JSON.stringify(result)` + `expirationTtl`;
  errors swallowed (warn only) — a cache write never breaks the request.
- **Read** (`get_fetch_cached`, `:65-80`): parse JSON → **full shape validation**
  (`is_valid_cached_fetch`, `:32-63`); corrupt/legacy entry is a miss.
  **Defense-in-depth**: also require `cached.requested_url === url` (`:75`).
- **Bypass** (orchestrator, doc 10): explicit `provider` mode, `skip_cache`, or any
  `skip_providers` (`:521`); writes also skipped under `skip_providers`
  (`build_and_cache`, `:604-610`).

---

## 06.2 `FetchRaceResult` (the cached value)
Unchanged from the original — add to `fetch/types.py` (extends doc 01):
`FetchRaceResult { requested_url, total_duration_ms, provider_used,
providers_attempted, providers_failed[], result: FetchResult,
alternative_results?[] }` (`:157-165`). It is a Pydantic model, so
`py-key-value`'s `PydanticAdapter` (de)serializes it directly and a
`ValidationError` on read is treated as a miss — this **replaces** the 30-line
hand-rolled `is_valid_cached_fetch`.

---

## 06.3 Design — `py-key-value`

`py-key-value-aio` API (verify exact kwargs against the installed version, RULE_04):
- Protocol `key_value.aio.protocols.key_value.AsyncKeyValue`:
  `get(key, collection=None) -> dict | None`,
  `put(key, value: dict, collection=None, ttl: float | None = None) -> None`,
  `delete(...)`, `ttl(...)`, plus `*_many` batch variants.
- Stores: `MemoryStore()`, `RedisStore(...)`, `DiskStore(...)`, etc.
- Adapter `PydanticAdapter[Model]` — typed get/put of a Pydantic model (handles
  `model_dump`/`model_validate`).
- Wrappers (stackable): `TTLClampWrapper`, `PrefixKeysWrapper`, `RetryWrapper`,
  `StatisticsWrapper`, … (optional).

```python
"""Fetch-result cache: backend-agnostic via py-key-value.

FetchCache stores FetchRaceResult under sha256(url) in the "fetch" collection with
a 36h TTL, over any AsyncKeyValue store (MemoryStore default; Redis/Disk via
config). A corrupt entry or a requested_url mismatch is a miss; a backend fault
degrades to a miss/no-op — the cache never raises into the request path.
"""
from __future__ import annotations
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols.key_value import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from omnifetch.fetch.types import FetchRaceResult
from omnifetch.fetch.util import hash_key
from omnifetch.logging import get_logger

_LOGGER = get_logger("fetch.cache")
_TTL_SECONDS = 129_600          # 36 hours, parity with KV_FETCH_TTL_SECONDS
_COLLECTION = "fetch"


class FetchCache:
    """Never-raising, FetchRaceResult-typed façade over an AsyncKeyValue store."""

    def __init__(self, store: AsyncKeyValue) -> None:
        # Confirm PydanticAdapter's constructor kwargs against the installed
        # py-key-value-aio (model/collection arg names may differ by version).
        self._store: PydanticAdapter[FetchRaceResult] = PydanticAdapter(
            store, FetchRaceResult, default_collection=_COLLECTION)

    async def get(self, url: str) -> FetchRaceResult | None:
        try:
            cached = await self._store.get(hash_key("fetch:", url))
        except Exception as exc:        # noqa: BLE001 — backend fault/corrupt → miss
            _LOGGER.debug("cache get failed: %s", exc)
            return None
        if cached is None or cached.requested_url != url:   # defense-in-depth, :75
            return None
        return cached

    async def set(self, url: str, value: FetchRaceResult) -> None:
        try:
            await self._store.put(hash_key("fetch:", url), value, ttl=_TTL_SECONDS)
        except Exception as exc:        # noqa: BLE001 — never break the request, :87-89
            _LOGGER.warning("cache set failed: %s", exc)
```

### Backend factory (config-driven, lazy imports keep extras optional)
```python
def build_cache_store(settings: ServerSettings) -> AsyncKeyValue:
    """Construct the configured AsyncKeyValue store (memory|redis|disk)."""
    backend = settings.cache_backend
    if backend == "memory":
        return MemoryStore()                          # see bounding note below
    if backend == "redis":
        from key_value.aio.stores.redis import RedisStore
        return RedisStore(url=settings.redis_url)     # confirm kwarg name
    if backend == "disk":
        from key_value.aio.stores.disk import DiskStore
        return DiskStore(directory=settings.disk_cache_path)
    raise ValueError(f"unknown cache backend: {backend}")
```
`FetchCache(build_cache_store(config.server))` is created once in the server
lifespan (doc 11) and injected into `run_fetch_race` — no module-global state.

### Decisions / rationale
- **Backend-agnostic by construction**: the orchestrator depends only on
  `FetchCache.get/set`; switching `memory↔redis↔disk` is a config flip
  (`OMNIFETCH_CACHE_BACKEND`). This is the GEMINI #2 advantage (a real persistent
  backend) without Gemini's mistake of hard-wiring Redis as mandatory infra — Redis
  is opt-in, `MemoryStore` stays the default for the stdio target.
- **Library owns TTL + serialization**: TTL is a `put(ttl=...)` kwarg; the
  `PydanticAdapter` serializes `FetchRaceResult`. The old `_MAX_ENTRIES` hand-roll,
  `time.monotonic` TTL, and `model_validate_json` are gone.
- **Cache-key parity preserved**: still `hash_key("fetch:", url)` = sha256 hex →
  byte-identical to TS (cross-impl vector, doc 13 §13.4), so a future shared Redis
  could be read by either implementation.
- **MemoryStore bounding**: confirm whether `MemoryStore` accepts a `max_entries`/
  size bound; if not, front it with a bounding wrapper (or accept TTL-only
  eviction). The 36h TTL already caps unbounded growth in practice; a hard cap is a
  belt-and-suspenders concern, not a correctness one. (Note this in code.)
- **Never raises**: both methods swallow backend errors → a fault degrades to
  "miss"/no-op (parity with the swallowed KV errors, `:77-79,87-89`). Optionally
  wrap the store in `py-key-value`'s `RetryWrapper`/`StatisticsWrapper` for resilient
  Redis ops + hit/miss metrics (free, stackable).

---

## 06.4 Config additions (server-level, `OMNIFETCH_` prefix)
Add to `ServerSettings` (`config.py:21-33`, extended in doc 04 §04.2):
```python
cache_backend: Literal["memory", "redis", "disk"] = "memory"
redis_url: str = ""                       # OMNIFETCH_REDIS_URL
disk_cache_path: str = ".cache/omnifetch" # OMNIFETCH_DISK_CACHE_PATH
```
Document in `README.md`'s config table + `.env.example`. `memory` requires no extra
infra; `redis`/`disk` pull the corresponding `py-key-value` extra (doc 06.6).

---

## 06.5 Acceptance criteria
1. `set(url, race)` then `get(url)` returns an **equal** `FetchRaceResult` (against
   a `MemoryStore`-backed `FetchCache`); a different URL is a miss.
2. **URL guard**: an entry whose `requested_url` differs from the lookup URL → miss
   (even on a matching hash key).
3. **TTL**: `put` is called with `ttl=129_600`; an expired entry is a miss (use a
   store that honors TTL, or assert the `ttl=` kwarg is passed).
4. **Corrupt / backend fault**: a store that raises on `get` → `get` returns None
   (no exception escapes); a store that raises on `put` → `set` logs a warning and
   does not raise.
5. **Backend-agnostic (the key new criterion)**: `run_fetch_race` produces identical
   results and identical cache hit/miss behavior whether `FetchCache` wraps a
   `MemoryStore` or a `RedisStore` (use `fakeredis`/a second `MemoryStore` instance)
   — the **orchestrator code is unchanged across backends**.
6. **Concurrency**: 100 concurrent `get`/`set` under `asyncio.gather` never raise.
7. **Key parity**: the stored key equals `hash_key("fetch:", url)` = TS
   `"fetch:"+sha256(url)` (cross-impl vector).
8. `mypy --strict` + ruff clean (`PydanticAdapter` generic typed to
   `FetchRaceResult`; the two `# noqa: BLE001` blind-excepts are justified by the
   never-raise contract).

## 06.6 Interfaces & dependencies
**Exposes:** `FetchCache`, `build_cache_store`, `FetchRaceResult` (via types).
**Consumes:** `py-key-value-aio`, `fetch/types`, `fetch/util` (`hash_key`),
`logging`, `config` (`ServerSettings`).
**Dependency delta:** add `py-key-value-aio` to `pyproject.toml` `dependencies`;
add the `redis`/`disk` extras only if those backends are used (keep them out of the
default install). `fakeredis` → `dev` group for the backend-agnostic test.
Regenerate `uv.lock`.
