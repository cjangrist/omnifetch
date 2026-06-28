# 12 — Observability (`fetch/shared/observability.py`)

> Re-targets two Cloudflare-specific layers to OpenTelemetry (already scaffolded in
> `omnifetch/telemetry.py`): the R2 forensic trace (`common/r2_trace.ts`, 294) →
> **OTEL spans**, and the Analytics-Engine scalars (`common/metrics.ts`, 151) →
> **OTEL metrics + a structured summary log**. Everything is a **no-op when
> telemetry is disabled** (the default), matching `telemetry.py`'s contract.
>
> **Tracing library choice (#6): OpenTelemetry, deliberately.** OTEL is the
> vendor-neutral standard — one instrumentation surface that exports to *any* OTLP
> backend (Jaeger, Tempo, Honeycomb, Datadog, Grafana Cloud, …), so it fits the
> cloud-agnostic posture (overview §0.1) with **no lock-in**. It is already a
> first-class, opt-in dependency (`opentelemetry-sdk` + OTLP exporter, the
> `telemetry` extra in `pyproject.toml`); this plan adds **no** other tracing lib,
> and there is zero overhead when it is off (the default).

---

## 12.1 The `TraceSink` seam (decouples engine from OTEL)

The orchestrator (doc 10) and HTTP client (doc 02) call a small sink interface,
never OTEL directly. Default sink is a no-op (zero overhead); an OTEL sink is
installed only when a tracer is active. The active sink propagates via a
`ContextVar` (replaces `AsyncLocalStorage`, `r2_trace.ts:14-39`).

```python
"""Fetch observability: OTEL spans + metrics behind a no-op-by-default sink.

run_fetch_race opens a span and binds a TraceSink in a ContextVar; provider
attempts and HTTP calls record onto it. When tracing is off, the sink is a no-op
and the only output is one structured summary log per fetch.
"""
from __future__ import annotations
import contextvars
from typing import Protocol
from omnifetch.fetch.types import FetchResult

class TraceSink(Protocol):
    def set_strategy(self, strategy: str) -> None: ...
    def set_active_providers(self, names: list[str]) -> None: ...
    def decision(self, action: str, details: dict[str, object]) -> None: ...
    def provider_start(self, provider: str, url: str) -> None: ...
    def provider_complete(self, provider: str, result: FetchResult, ms: float) -> None: ...
    def provider_error(self, provider: str, error: str, ms: float) -> None: ...
    def record_http(self, provider: str, method: str, url: str,
                    status: int, ms: float, size: int) -> None: ...

_active: contextvars.ContextVar[TraceSink] = contextvars.ContextVar("trace_sink")

def active_sink() -> TraceSink:
    return _active.get(_NOOP)        # _NOOP is a module-level NoOpSink instance
```

### Mapping (what each call becomes)
| TS (`r2_trace.ts`) | OTEL span model |
|---|---|
| `TraceContext` per `run_fetch_race` (`:83-100`) | one **parent span** `fetch.race` (attrs: url-host, strategy, skip, cache_hit) |
| `record_provider_start/complete/error` (`:118-151`) | one **child span** `fetch.provider/<name>` (status=ok/error, duration, content_length) |
| `record_http_call` (`:153-167`, from `http.ts:54-65`) | one **child span** `fetch.http` (method, redacted url, status, bytes) — or a span event on the provider span |
| `record_decision` (`:110-116`) | **span events** on the parent (`waterfall_step`, `breaker_match`, `cache_hit`, `waterfall_fast_fail`, …) |
| `flush_background` → R2 JSON blob (`:170-256`) | **optional** full-fidelity JSON dump to `OMNIFETCH_TRACE_DUMP_DIR` (off by default); spans already cover the queryable layer |

> The R2 blob captured **unredacted** request/response bodies for forensics. Do
> **not** put raw bodies on spans (they bloat + can leak). Gate the full JSON dump
> (with redaction via `http._redact`) behind an explicit env flag for debugging.

### No-op default
`NoOpSink` implements every method as `pass`. `active_sink()` returns it unless
`run_fetch_race` bound an `OtelSink`. The engine code is identical in both modes —
the only branch is at sink construction, mirroring `telemetry.py:38-51`.

---

## 12.2 Metrics (`common/metrics.ts` Dataset C → OTEL)

Port the `FetchMetric` fields (`metrics.ts:126-139`) — emitted once per
`run_fetch_race` on every return/throw path (`fetch_orchestrator.ts:503-520` is
the `emit` closure). In Python, a single `emit_fetch_metric(m)`:

- **Always** writes one structured INFO log (RULE_09 #1): `provider_used`,
  `outcome`, `total_ms`, `waterfall_depth`, `providers_failed_count`, `host`,
  `cache_hit`, `content_length`, `skip_providers`. This gives observability even
  with OTEL off.
- **When a meter is active**, also records OTEL instruments:
  - `Counter omnifetch.fetch.requests` (attrs: `outcome, provider_used, breaker,
    error_class, cache_hit, skip_providers`).
  - `Histogram omnifetch.fetch.duration_ms` (attrs: `outcome, provider_used`).
  - `Histogram omnifetch.fetch.waterfall_depth`.
  - `Histogram omnifetch.fetch.content_length`.
- `outcome ∈ {resolved, exhausted, cache_hit, explicit, fast_fail, error,
  no_providers}` — same vocabulary as `metrics.ts:128`.
- **Never raises** (parity with `safe_write`, `metrics.ts:46-57`): wrap emission
  in try/except → swallow + debug-log. Metrics must not break a fetch.

The request-level (`Dataset A`) and search (`Dataset B`) metrics are **out of
scope** (not the fetch path).

---

## 12.3 `telemetry.py` extension
Add lazy accessors `get_tracer()` / `get_meter()` that return the OTEL
tracer/meter when `configure_telemetry` activated the SDK, else `None`. The
fetch-engine builds an `OtelSink`/metric-emitter only when these are non-`None`.
Keep OTEL imports lazy (the `telemetry` optional extra), exactly as
`telemetry.py:52-66` already does.

---

## 12.4 Per-request id in logs (#9)
TS stamps every log line for a tool call with a request id
(`run_with_request_id(crypto.randomUUID())`, `tools.ts:307`; its logger reads it
from `AsyncLocalStorage`). Port it as a **contextvar + a logging filter** so
concurrent fetches are trivially separable in the structured logs — the cheap
day-to-day correlation handle, complementary to (and cheaper than) OTEL spans.

```python
import contextlib, contextvars, logging, uuid
from collections.abc import Iterator

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-")

@contextlib.contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind a fresh request_id for one fetch (mirrors TS run_with_request_id);
    propagates across awaits + asyncio tasks via contextvars."""
    rid = request_id or uuid.uuid4().hex
    token = request_id_var.set(rid)
    try:
        yield rid
    finally:
        request_id_var.reset(token)

class RequestIdFilter(logging.Filter):
    """Inject the active request_id onto every log record (default '-')."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True
```
Wire-up:
- Add `RequestIdFilter` to the package handler in `logging.configure_logging`
  (`logging.py`) and put `%(request_id)s` in `_LOG_FORMAT`.
- **Bind once per fetch** at the top of `run_fetch_race` (doc 10 §10.4 step 0):
  `with request_context(): …`. Every provider / HTTP / decision log during that race
  then carries the same id. (`uuid4()` at runtime is fine — the `Date.now()`/`random`
  ban applies to *workflow scripts*, not engine code.)
- Optional: also set `request_id` as an attribute on the OTEL parent span so logs ↔
  traces join on it.

---

## 12.5 Acceptance criteria
1. **Off by default**: with no `OTEL_TRACES_EXPORTER`, a fetch runs with `NoOpSink`
   (no spans), emits exactly one structured summary log, and adds **no** measurable
   latency (assert sink is the no-op instance).
2. **On**: with `OTEL_TRACES_EXPORTER=console`, a single fetch produces one parent
   `fetch.race` span with child provider spans and `waterfall_step`/`breaker_*`
   events; an error path sets span status=error.
3. **HTTP spans/events** carry a **redacted** URL — assert no API key appears in
   any span attribute or log.
4. **Metrics**: `emit_fetch_metric` records the counter+histograms when a meter is
   active; with no meter it only logs; a raised exception inside emission is
   swallowed (inject a faulty meter, assert the fetch still returns).
5. **ContextVar propagation**: the HTTP client's `record_http` lands on the same
   trace as the orchestrating coroutine across `await`s and inside `asyncio` tasks
   (set the sink in `run_fetch_race`, assert provider-task HTTP calls attach).
6. **Request-id (#9)**: two concurrent in-memory `fetch` tool calls produce log
   records with **distinct** `request_id`s and no cross-contamination (assert via
   `caplog` that each call's engine logs share one id, different between calls); a
   call with no active context logs `request_id="-"`.
7. `mypy --strict` (Protocol typing) + ruff clean.

## 12.6 Interfaces
**Exposes:** `TraceSink`, `NoOpSink`, `OtelSink`, `active_sink`, `bind_sink`,
`emit_fetch_metric`, and (#9) `request_context`, `request_id_var`,
`RequestIdFilter`. **Consumes:** `omnifetch/telemetry` (lazy tracer/meter),
`fetch/types`, `logging`. **Consumed by:** `orchestrator.py` (binds
`request_context`), `concurrency.py`, `http.py` (the `record_http` hook),
`logging.configure_logging` (installs `RequestIdFilter`).
