# 02 — Async HTTP client (`fetch/shared/http.py`)

> The single most performance-sensitive shared tool. Every provider's network I/O
> flows through `http_json` / `http_text`. Get the **shared, pooled, bounded**
> client right and the whole engine inherits good throughput + safety.
>
> Source: `common/http.ts` (187 lines). Read `14-concurrency-performance.md`
> before implementing this.

---

## 02.1 What the TS version does (`common/http.ts`)

`http_core(provider, url, options)` (`:41-164`):
1. Records the request into the active trace (`:47-66`) — re-targeted to OTEL in 12.
2. Logs request with a **redacted** URL (`sanitize_url` strips `api_key,key,token,app_id,x-api-key,apikey` query params, `:23-38`).
3. `await fetch(url, options)` (`:77`).
4. **Size guard A** — reject if `content-length` header > **5 MB** (`MAX_RESPONSE_BYTES`, `:21,82-89`); cancels body.
5. **Size guard B** — stream-read via reader, count bytes, abort if running total > 5 MB (catches chunked/lying `content-length`) (`:91-114`).
6. Records full response into trace (`:117`).
7. `okOrExpected = res.ok || expectedStatuses.includes(status)` (`:119-122`).
8. On not-ok: parse body for `message|error|detail`, then **status→error map** (`:124-152`):
   - `401` → `ProviderError(API_ERROR, "Invalid API key")`
   - `403` → `ProviderError(API_ERROR, "API key does not have access to this endpoint")`
   - `429` → `handle_rate_limit(provider)` (raises `RATE_LIMIT`)
   - `>=500` → `ProviderError(PROVIDER_ERROR, "... internal error (status): msg")`  ← **retryable class**
   - else → `ProviderError(API_ERROR, "... error (status): msg")`
9. Returns `{ raw, status }`.

Wrappers:
- `http_json<T>` (`:167-176`) — parse JSON or raise `API_ERROR "Invalid JSON response"`.
- `http_text` (`:179-186`) — return raw string.

`HttpOptions extends RequestInit { expectedStatuses?: number[] }` (`:8-11`).

**Why the error classes matter:** `PROVIDER_ERROR` (5xx, network) is the only
class the orchestrator treats as retryable/failover-friendly without semantic
meaning; `RATE_LIMIT`/`API_ERROR`/`INVALID_INPUT`/`NOT_FOUND` carry intent the
orchestrator (doc 10) and providers branch on. **Preserve the exact mapping.**

---

## 02.2 Python design

### Shared client lifecycle (performance foundation)
ONE `httpx.AsyncClient`, created at server startup, closed at shutdown, reused by
every request → connection pooling + HTTP/2 + keep-alive. Do **not** create a
client per call (that was the implicit Workers model and it is wasteful in a
long-lived process).

**Explicit DI — no global/contextvar client (#6).** The client is **passed
explicitly**: each provider receives it at construction (doc 07) and calls
`http_json(self._client, …)`; the server lifespan (doc 11) owns its lifecycle.
There is **no** module-level `set_http_client`/`ContextVar` for the client — hidden
global client state carries a "must-set-before-first-call" ordering foot-gun and a
subtler test setup. (The trace **sink** legitimately uses a contextvar in doc 12 —
that's ambient request context; the *client* is a dependency and is injected.)

```python
"""Async HTTP core for all fetch providers.

A single pooled httpx.AsyncClient is **passed explicitly** to http_json/http_text/
http_raw (the provider holds it; see the DI note above). All three enforce a 5 MB
streamed response cap and translate transport + status outcomes into the engine's
ProviderError taxonomy so the orchestrator can route failover vs. fast-fail
uniformly.
"""
from __future__ import annotations
import asyncio
import json
import re
from typing import Any
from urllib.parse import urlsplit
import httpx
from tenacity import (AsyncRetrying, retry_if_exception, stop_after_attempt,
                      wait_exponential_jitter)
from omnifetch.fetch.shared.types import ErrorType, ProviderError
from omnifetch.fetch.shared.util import handle_rate_limit
from omnifetch.logging import get_logger

_LOGGER = get_logger("fetch.http")
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_SENSITIVE = {"api_key", "key", "token", "app_id", "x-api-key", "apikey"}
# No module-level client (#6): the httpx.AsyncClient is injected per call. Only the
# per-host Semaphore registry (below) and constants are module state.
```

### Client construction (in `server.py` lifespan — see 11)
```python
limits = httpx.Limits(max_connections=100, max_keepalive_connections=40,
                      keepalive_expiry=30.0)
client = httpx.AsyncClient(
    http2=True, follow_redirects=True, limits=limits,
    timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0),
    headers={"user-agent": "omnifetch/<version>"},
)
```
- `max_connections=100`: comfortably exceeds the **peak in-flight provider count**
  during a parallel waterfall step (≤3 concurrent providers per request) ×
  expected request concurrency. Tune in 14.
- **Per-host cap (#1)**: `httpx.Limits` has **no** per-host knob (unlike aiohttp's
  `limit_per_host`), so a per-host `asyncio.Semaphore` enforces it inside `_request`
  (§ below), default 20 (`OMNIFETCH_HTTP_LIMIT_PER_HOST`). Stops one
  slow/duplicated host (e.g. kimi via Scrapfly *while* a parallel step also hits
  Scrapfly) from monopolizing the global pool.
- Per-call timeout still passed explicitly (below) — pool timeout is a backstop.
- `http2=True` helps providers that support it; harmless otherwise.

### Per-host cap (#1) + bounded transient retry (#4)
```python
# (#1) Per-host concurrency cap. httpx has no native per-host limit, so a
# Semaphore-per-host gates _request. The dict is mutated only between awaits on the
# single event loop, so get/set is atomic — no lock needed. Bounded by the small
# set of distinct provider hosts (~30) → negligible memory.
_LIMIT_PER_HOST = 20                       # OMNIFETCH_HTTP_LIMIT_PER_HOST
_host_locks: dict[str, asyncio.Semaphore] = {}

def _host_semaphore(url: str) -> asyncio.Semaphore:
    host = urlsplit(url).hostname or ""
    sem = _host_locks.get(host)
    if sem is None:
        sem = asyncio.Semaphore(_LIMIT_PER_HOST)
        _host_locks[host] = sem
    return sem

# (#4/#7) Optional bounded retry of TRANSIENT failures only, via tenacity. Default
# 0 = strict TS parity (the WATERFALL is the failover mechanism; this is not a
# substitute). A value >0 retries ONLY ProviderError(PROVIDER_ERROR) — 5xx + network
# blips — never RATE_LIMIT / API_ERROR / INVALID_INPUT / NOT_FOUND, so it can't
# subvert the orchestrator's failover/fast-fail routing. Keep the total inside the
# provider deadline (note below + doc 14 §14.3).
_TRANSIENT_RETRIES = 0                      # OMNIFETCH_HTTP_TRANSIENT_RETRIES

def _is_transient(exc: BaseException) -> bool:
    """tenacity predicate: retry only transient provider errors (5xx/network)."""
    return (isinstance(exc, ProviderError)
            and exc.error_type is ErrorType.PROVIDER_ERROR)
```

### Core function
```python
async def _request(client: httpx.AsyncClient, provider: str, url: str,
                   **kw: Any) -> tuple[str, int]:
    """Host-capped + (optionally) transient-retried wrapper around one attempt."""
    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_transient),       # only transient PROVIDER_ERROR
        stop=stop_after_attempt(1 + _TRANSIENT_RETRIES),  # =1 → no retry (default)
        wait=wait_exponential_jitter(initial=0.25, max=2.0),
        reraise=True,                                  # surface the real error, not RetryError
    ):
        with attempt:
            async with _host_semaphore(url):
                return await _do_request(client, provider, url, **kw)
    raise AssertionError("unreachable")  # pragma: no cover — AsyncRetrying returns/raises


async def _do_request(client: httpx.AsyncClient, provider: str, url: str, *,
                      method: str = "GET",
                      headers: dict[str, str] | None = None,
                      content: str | bytes | None = None,
                      timeout_s: float | None = None,
                      expected_statuses: tuple[int, ...] = ()) -> tuple[str, int]:
    _LOGGER.debug("HTTP %s %s", method, _redact(url))
    try:
        async with client.stream(
            method, url, headers=headers, content=content,
            timeout=timeout_s if timeout_s is not None else httpx.USE_CLIENT_DEFAULT,
        ) as resp:
            raw = await _read_capped(resp, provider)
    except httpx.HTTPError as exc:                  # transport/timeout/network
        raise ProviderError(ErrorType.PROVIDER_ERROR, str(exc), provider) from exc
    _raise_for_status(provider, resp.status_code, raw, expected_statuses)
    return raw, resp.status_code
```
> **Retry budget**: each attempt keeps its own httpx `timeout_s`, so N retries can
> cost up to `N × (timeout_s + backoff)`. When `_TRANSIENT_RETRIES > 0`, the
> executors (doc 10) should wrap the provider attempt in `provider_timeout(timeout_ms)`
> (doc 01) so the *total* stays inside the provider's deadline. With the default 0,
> behavior is byte-for-byte the TS model.

### Streamed 5 MB cap (`_read_capped`) — parity with guards A+B
```python
async def _read_capped(resp: httpx.Response, provider: str) -> str:
    total = 0
    chunks: list[bytes] = []
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            await resp.aclose()
            raise ProviderError(ErrorType.API_ERROR,
                                f"Response too large (>{_MAX_RESPONSE_BYTES} bytes)",
                                provider)
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")
```
Streaming + running byte-count replicates **both** TS guards (header-based and
chunked) in one pass. `decode(errors="replace")` mirrors the tolerant
`TextDecoder`.

### Status → error mapping (`_raise_for_status`) — exact parity with `:124-152`
```python
def _raise_for_status(provider: str, status: int, raw: str,
                      expected: tuple[int, ...]) -> None:
    if 200 <= status < 300 or status in expected:
        return
    msg = _safe_message(raw, status)
    if status == 401:
        raise ProviderError(ErrorType.API_ERROR, "Invalid API key", provider)
    if status == 403:
        raise ProviderError(ErrorType.API_ERROR,
            "API key does not have access to this endpoint", provider)
    if status == 429:
        handle_rate_limit(provider)            # raises RATE_LIMIT
    if status >= 500:
        raise ProviderError(ErrorType.PROVIDER_ERROR,
            f"{provider} API internal error ({status}): {msg}", provider)
    raise ProviderError(ErrorType.API_ERROR,
        f"{provider} error ({status}): {msg}", provider)
```
`_safe_message` mirrors `:125-129` (prefer JSON `message|error|detail`, else
`reason_phrase`).

### Public wrappers
```python
async def http_json(client: httpx.AsyncClient, provider: str, url: str,
                    **kw: Any) -> Any:
    raw, _ = await _request(client, provider, url, **kw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(ErrorType.API_ERROR,
                            f"Invalid JSON response from {provider}", provider) from exc

async def http_text(client: httpx.AsyncClient, provider: str, url: str,
                    **kw: Any) -> str:
    raw, _ = await _request(client, provider, url, **kw)
    return raw

async def http_raw(client: httpx.AsyncClient, provider: str, url: str,
                   **kw: Any) -> tuple[str, int]:
    """Return (body, status) for providers that branch on the status code — e.g.
    supadata's 202 async-job path. Pass `expected_statuses=(202,)` to suppress the
    status→error mapping for codes you handle yourself (doc 09 §09.1)."""
    return await _request(client, provider, url, **kw)
```
The `client` is always the **first positional arg** — providers pass `self._client`
(doc 07), so the call site reads `http_json(self._client, self.name, url, …)`.
(`http_json` is typed `-> Any` because each provider casts to its own
`TypedDict`/parse; this is the one sanctioned `Any` and is why providers validate
shape immediately. Consider a generic `http_json[T]` returning `T` via a
`type[T]` arg if stricter typing is desired.)

### `_redact(url)` — parity with `sanitize_url` (`:23-38`)
Rebuild the URL with `_SENSITIVE` query params replaced by `[REDACTED]`, slice to
200. Used in every request log line. **Critical**: many providers pass the key in
the query string (scrapfly, scrapedo, scrapingbee, scraperapi, diffbot, opengraph,
scrappey) — without redaction the key leaks into stderr logs.

---

## 02.3 Trace hook (forward-reference to 12)
`http_core` records every call into the active R2 trace. In Python, `_request`
emits an **OTEL span** (`fetch.http`) with attributes `{provider, method,
redacted_url, status, duration_ms, response_bytes}` when a tracer is active, else
no-op. Keep the hook a single call to `observability.record_http(...)` so this
module has no hard OTEL dependency (lazy/optional, mirroring `telemetry.py`).

---

## 02.4 Per-provider timeout integration
Providers pass `timeout_s = config.timeout_ms / 1000` to `http_json/http_text`.
This sets httpx's per-call timeout (overrides the client default). The orchestrator
additionally wraps the whole provider attempt in `provider_timeout()` (doc 01/10)
as a hard ceiling that also bounds non-httpx work (e.g. supadata's poll loop).
Two layers, intentional — see 14.

---

## 02.5 Acceptance criteria
Use `respx` (httpx mock) or a local `pytest-httpserver`.
1. **5 MB cap**: a mocked 6 MB streamed body raises `ProviderError(API_ERROR,"Response too large...")` and reads ≤ ~5 MB (does not buffer the whole 6 MB first). Test both an honest oversized `content-length` and a chunked body with no/lying length.
2. **Status map**: 401→`API_ERROR "Invalid API key"`; 403→`API_ERROR "...access..."`; 429→`RATE_LIMIT`; 500→`PROVIDER_ERROR "...internal error (500)..."`; 418→`API_ERROR "...error (418)..."`; `expected_statuses=(404,)` + 404 → returns body, no raise (parity with `:121`).
3. **JSON**: `http_json` returns parsed dict; non-JSON body raises `API_ERROR "Invalid JSON response from <p>"`.
4. **Redaction**: `_redact("https://x?api_key=SECRET&q=1")` contains `[REDACTED]`, never `SECRET`; assert no log record contains the key.
5. **Pooling + explicit DI (#6)**: 50 concurrent `http_text` calls reuse the one **injected** client (no per-call client; no "too many open files"). Assert the module has **no** `set_http_client`/`_client` contextvar and that a test passes a `respx`-mocked client directly as the `client` argument.
6. **Transport error**: a connection error surfaces as `ProviderError(PROVIDER_ERROR)` (so the orchestrator treats it as failover-eligible).
7. **Per-host cap (#1)**: 50 concurrent `http_text` calls to the *same* host never exceed `_LIMIT_PER_HOST` simultaneously in flight (instrument the semaphore or a counting mock transport); concurrent calls to *different* hosts are not throttled against each other.
8. **Bounded transient retry (#4)**: with `OMNIFETCH_HTTP_TRANSIENT_RETRIES=1` — a `500` then `200` → one retry, returns the 200; `500` every time → fails after exactly **one** retry; a `429` → `RATE_LIMIT` with **no** retry; `401`/`INVALID_INPUT`/`NOT_FOUND` never retried. With the default `=0`, **no** retry occurs (strict TS parity) — assert `_do_request` is called exactly once.
9. `mypy --strict` + ruff clean.

## 02.6 Interfaces
**Exposes:** `http_json(client, …)`, `http_text(client, …)`, `http_raw(client, …)`,
`_redact` (test). **No** `set_http_client` — the client is injected (#6).
**Consumes:** `fetch/types`, `fetch/util` (`handle_rate_limit`), `logging`,
`observability` (optional hook), `httpx`.

## 02.7 Dependency to add
`httpx` (with `http2` extra → `httpx[http2]`) and **`tenacity`** (the bounded
transient retry, #7) in `pyproject.toml` `dependencies`. `respx` in the `dev` group.
Pin exact versions per the project's pinning policy; regenerate `uv.lock`.

The per-host cap (#1) is hand-rolled with stdlib `asyncio`. The bounded retry
(#4/#7) uses **`tenacity`** (`AsyncRetrying`) — but **correctly**, unlike Gemini's
"retry every 429/5xx inside the client" (which fights failover): the `retry_if_exception`
predicate fires **only** on transient `PROVIDER_ERROR`, attempts are bounded by
`OMNIFETCH_HTTP_TRANSIENT_RETRIES`, backoff is jittered, and it is **off by
default**. `RATE_LIMIT`/`API_ERROR`/`INVALID_INPUT`/`NOT_FOUND` are never retried,
so the waterfall remains the failover mechanism.

## 02.8 No-raw-HTTP-client rule (#5 — enforced invariant)
**Every** network call in the engine goes through `http_json` / `http_text` /
`http_raw` (this module). Providers, the orchestrator, and the GitHub handlers
**must not** `import requests`, use `urllib.request`, or construct their own
`httpx.AsyncClient()` — they receive the shared client and pass it in. This keeps
the 5 MB cap, the status→error taxonomy, redaction, the per-host cap, the retry, and
the trace hook **uniform and impossible to bypass**.

Two TS sites used a bare `fetch()` and must be routed through the shared client on
the way over:
- **supadata's 202 path** (`supadata/index.ts:97`) — needs the status code, so use
  `http_raw(client, "supadata", url, expected_statuses=(202,))` and branch on the
  returned status (doc 09 §09.1).
- **GitHub raw-file / wiki fetches** (`handlers-file.ts:153/195`) — route through
  the shared client with the `"github"` provider name (doc 09b §09b.4).

Guarded by a static-grep test (doc 13 §13.5): grepping `src/omnifetch/fetch/` for
`httpx.AsyncClient(`, `import requests`, or `urllib.request` outside `http.py`
(and tests) must return nothing.
