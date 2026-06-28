# 14 — Concurrency & performance (cross-cutting design constraints)

> Read this **before** implementing `02-async-http-client.md` and
> `10-orchestrator-concurrency.md`. It is the rationale + the rules the rest of the
> plan assumes. The whole engine is **I/O-bound** (network waits dominate; the only
> CPU work is small regex + JSON parsing of ≤5 MB bodies), so `asyncio` on a single
> thread is the right model and the design optimizes for **tail latency** and
> **connection reuse**, not CPU parallelism.

---

## 14.1 Execution model
- One process, one event loop, cooperative scheduling. Every provider call is a
  coroutine; concurrency comes from `await`-ing many at once, not threads.
- **No shared mutable state across coroutines except the cache** (lock-guarded,
  doc 06). The `RaceContext` accumulators (`attempted`/`failed`/`winners`) are
  mutated **only** by the single orchestrating coroutine — the `asyncio.wait` loop
  processes completed tasks one at a time — so they need **no lock**. Keep it that
  way: provider tasks must never touch `RaceContext` directly.
- Prefer **`uvloop`** in production (`asyncio.set_event_loop_policy` or
  `uvloop.run`) for a measurable event-loop speedup; keep it optional + behind the
  CLI/`__main__` so tests run on the stdlib loop. (Stretch; not required.)

---

## 14.2 The multi-winner race (the one hard primitive)

The parallel waterfall step (doc 10 `run_parallel`) is the core concurrency
primitive. Requirements:
1. Launch all available providers in the step **concurrently**.
2. Return as soon as **`target_count`** of them produce a *passing* result
   (`is_fetch_failure` False).
3. **Cancel** the still-running losers the moment the target is met.
4. Record only **pre-settle** failures; **suppress** anything from cancelled tasks.

Why `asyncio.wait(FIRST_COMPLETED)` in a loop (not `gather`, not `as_completed`,
not bare `TaskGroup`):
- `gather` waits for **all** → defeats requirement 2/3 (no early return).
- `as_completed` yields in completion order but leaves cancellation/bookkeeping to
  you and is awkward to stop early cleanly.
- `TaskGroup` is great for "all must succeed"; here we **want** to abandon losers,
  and TaskGroup's exit semantics (cancel-all-on-error, await-all-on-success) fight
  the "first-N-wins, cancel-rest" pattern. Manual `wait`-loop + `finally`-cancel is
  clearer and exactly matches the TS `resolved`-flag settle.

**Cancellation safety**: `task.cancel()` raises `CancelledError` inside the
provider coroutine at its next `await` — which is virtually always the in-flight
`httpx` request. `httpx` cancels a request cleanly (closes the stream, returns the
connection to the pool or drops it). So cancelling a loser **frees its connection**
and **stops billing** that provider. After cancelling, `await
asyncio.gather(*pending, return_exceptions=True)` lets every cancellation fully
settle before the step returns → **no orphaned tasks**, no "Task was destroyed but
it is pending" warnings (pinned by a `-W error` test, doc 13 §13.3).

> **This is a deliberate improvement over the TS engine**, which let losing
> providers run to completion and merely discarded their results (`fetch_
> orchestrator.ts:305-318`). Cancelling them saves provider cost and frees
> connections sooner. Behavior visible to the caller is identical (same winners,
> same `providers_failed`); only wasted work is removed. Document this divergence in
> the code.

---

## 14.3 Timeouts — two layers, on purpose
1. **httpx per-call timeout** (`timeout_s = config.timeout_ms/1000`, doc 02) — the
   network-level deadline (connect/read/write). Bounds the common case.
2. **`provider_timeout(timeout_ms)`** wrapping the whole provider attempt (doc
   01/10) — a hard ceiling that also bounds **non-httpx** work, notably supadata's
   `poll_job` loop (`supadata/index.ts:49-75`) and kimi's proxy round-trip. Without
   layer 2 a poll loop could outlive its budget.

**Optional transient retry (#4) interacts with both layers.** The HTTP client can
do a single bounded retry of a transient `PROVIDER_ERROR` (5xx/network) before the
error propagates and the waterfall fails over (doc 02, default
`OMNIFETCH_HTTP_TRANSIENT_RETRIES=0`). It retries **only** transient errors —
never `RATE_LIMIT`/`API_ERROR`/`INVALID_INPUT`/`NOT_FOUND` — so it cannot subvert
failover/fast-fail routing. Because each retry costs another `timeout_s + backoff`,
**when retries are enabled, wrap the provider attempt in `provider_timeout(timeout_ms)`
in the executors** (layer 2) so the total can't exceed the provider's deadline. The
default 0 keeps byte-for-byte TS behavior; treat any non-zero value as a deliberate
latency-for-resilience trade backed by latency data.

There is **no global race deadline** (parity with TS, which relies on per-provider
timeouts + the Workers wall-clock). If you want a hard end-to-end SLA, add an outer
`asyncio.timeout(total_budget)` around `run_fetch_race` — but size it above the
slowest single step (a sequential step of 12 providers × up-to-30 s is the
worst case; in practice it short-circuits on the first success). Recommended:
**leave it off** initially; revisit with real latency data.

---

## 14.4 Shared client + connection-pool sizing
ONE pooled `httpx.AsyncClient` for the whole process (doc 02/11). Pool math:

- **Peak in-flight connections per fetch call** = the widest concurrent step =
  **3** (the `[scrapfly, scrapedo, decodo]` parallel step), plus ≤1 breaker = ~3–4.
- **Peak across the server** = `concurrent_fetch_calls × ~3`.
- With `max_connections=100`, the pool comfortably serves **~25–30 concurrent
  fetch tool calls** at full parallel-step fan-out before queueing. The MCP server
  typically sees far less concurrency than that.
- `max_keepalive_connections=40`, `keepalive_expiry=30s` → warm connections to the
  busiest providers (tavily/firecrawl/the parallel-step trio) are reused across
  calls, cutting TLS-handshake latency on the hot path.
- **Tune via config** if you run high concurrency: expose
  `OMNIFETCH_HTTP_MAX_CONNECTIONS` etc. (uppercase config, RULE_09 #2). Don't
  hard-fail on pool exhaustion — httpx queues; the per-call timeout bounds the wait.

**Per-host cap (#1, enforced).** Most steps hit **distinct hosts** (one per
provider), so global-pool saturation is the usual concern — but a few paths *can*
pile onto one host (kimi routing through Scrapfly **while** the `[scrapfly, scrapedo,
decodo]` parallel step also hits Scrapfly; or many concurrent fetches of
youtube/supadata). `httpx.Limits` has **no per-host knob** (aiohttp's
`limit_per_host` does), so the HTTP client enforces it with an
`asyncio.Semaphore`-per-host (doc 02, default `_LIMIT_PER_HOST=20`,
`OMNIFETCH_HTTP_LIMIT_PER_HOST`). This caps the connections any single upstream can
hold, so one slow/duplicated host can't starve the rest of the pool. The semaphore
dict is mutated only between `await`s on the single loop → lock-free; it holds ~one
entry per distinct provider host (negligible memory).

---

## 14.5 CPU work on the loop (keep it off the critical path)
The only CPU work is: title regex (doc 01, microseconds), `json.loads` of a body
(≤5 MB, C-implemented, sub-millisecond for typical pages), and the failure-pattern
substring scans (doc 05, linear over ≤ a few hundred KB). All are negligible vs.
network latency and run inline.
- **If** a pathological 5 MB JSON body ever shows up as a loop-stall in profiling,
  offload that single `json.loads` via `await asyncio.to_thread(json.loads, raw)`.
  Don't do this preemptively — it adds thread-hop latency to every call for no gain.
- The 5 MB **streaming cap** (doc 02) is itself a perf + safety guard: it bounds
  both memory and the largest possible parse, and it aborts early on oversized
  bodies instead of buffering them.

---

## 14.6 Avoiding head-of-line blocking
- The **sequential** waterfall tier (12 providers) is intentionally serial (it's
  the last-resort tier; running 12 paid scrapers in parallel would be wasteful).
  Its latency is bounded by "first success" — it stops at `target_count`
  (`run_sequential`, doc 10). Order matters: cheapest/fastest-likely first
  (`jina, spider, you, …`) — preserve the TS order.
- The **parallel** tiers exist precisely to avoid HOL blocking on a slow provider:
  3 race, first winner returns, rest cancelled (§14.2).
- The MCP server processes tool calls concurrently; one slow fetch never blocks
  another (each runs its own race coroutine; the shared client multiplexes I/O).

---

## 14.7 Memory & backpressure
- **Cache** growth is bounded by the **36h TTL** (`py-key-value` `put(ttl=...)`,
  doc 06); if a hard entry cap is wanted, front the `MemoryStore` with a bounding
  wrapper (the Workers KV was externally bounded; an in-process store relies on
  TTL + optional capping).
- **Response bodies** are capped at 5 MB and streamed (no full double-buffering).
- **Backpressure**: the httpx pool + per-call timeout are the natural backpressure
  mechanism. If you front the server with HTTP transport at high RPS, add a
  semaphore around `run_fetch_race` (`OMNIFETCH_MAX_INFLIGHT_FETCHES`) to cap total
  concurrent races and protect provider rate limits. Off by default.

---

## 14.8 Performance targets & how to measure
Targets (typical public URL, provider keys configured):
- **Cache hit**: < 5 ms (in-memory, no network) — vs. KV's network round-trip.
- **Happy path** (first solo provider succeeds): ≈ that provider's own latency
  (tavily/firecrawl, ~1–5 s) + < 5 ms engine overhead.
- **Parallel-step win**: ≈ the *fastest* of the racing providers (not the slowest)
  — the whole point of the race.
- **Engine overhead** (excluding provider I/O): < 5 ms p99 (title regex + failure
  scan + cache + span bookkeeping).

Measurement harness (doc 13 can host it):
- Microbench the engine with **fake providers** at fixed delays → isolates engine
  overhead from network. Assert a `[100ms, 10ms]` parallel race returns in ~10–15
  ms (proves first-winner + cancellation, not 100 ms).
- Load-test the in-memory `Client` with `asyncio.gather` of N concurrent `fetch`
  calls against mocked providers → verify pool sizing holds (no errors, latency
  flat to ~25 concurrent).
- Track `omnifetch.fetch.duration_ms` histogram + `waterfall_depth` (doc 12) in a
  real deployment for p50/p95/win-share per provider (the TS team tuned the
  waterfall order from exactly this data — see the `env.ts:176-179` sociavault
  timeout comment).

---

## 14.9 Divergences from TS that are net-positive (call them out in code)
| Change | Why it's better in a long-lived Python process |
|---|---|
| Cancel losing parallel providers (vs. let-run-and-discard) | saves provider $, frees connections sooner; same observable result |
| One shared pooled `httpx.AsyncClient` (vs. Workers' per-isolate `fetch`) | TLS/keepalive reuse across calls → lower hot-path latency |
| Enforced per-host `Semaphore` cap (#1) | httpx lacks a per-host limit; prevents one host monopolizing the global pool |
| `py-key-value` cache, `MemoryStore` default (vs. Cloudflare KV) | no network hop on hit (< 5 ms); backend-agnostic — Redis/Disk swap in by config with zero engine changes |
| `contextvars` trace (vs. `AsyncLocalStorage`) | same propagation semantics, native |
| Drop the `initialize_providers` idempotency guard + singleton swap | single process → construct once in lifespan, inject; no isolate races |

---

## 14.10 Anti-patterns to avoid (will cause bugs/regressions)
- ❌ Creating an `httpx.AsyncClient` per request/provider → connection churn, no
  keepalive, file-descriptor exhaustion under load.
- ❌ `gather`-ing a parallel step and post-filtering → no early return, no
  cancellation, pays the slowest provider every time.
- ❌ Mutating `RaceContext` from inside provider tasks → reintroduces the data
  races the lock-free design avoids; keep mutation in the orchestrating coroutine.
- ❌ Forgetting to `await` the post-cancel `gather` → "Task was destroyed but it is
  pending" warnings + leaked connections.
- ❌ A global `asyncio.Lock` around the whole race → serializes everything, kills
  concurrency. Only the cache needs a lock.
- ❌ Blocking calls (`requests`, `time.sleep`, sync file I/O) anywhere in the async
  path → stalls the entire event loop. Use `httpx.AsyncClient`, `asyncio.sleep`,
  `asyncio.to_thread` for any unavoidable sync work.
- ❌ Putting raw response bodies on OTEL spans → bloat + secret leakage; use the
  redacted, gated JSON dump (doc 12) for forensics.

---

## 14.11 Consolidated dependency delta
`pyproject.toml` `dependencies`: add **`httpx[http2]`** and **`py-key-value-aio`**
(the backend-agnostic cache, doc 06 — `MemoryStore` needs no extras; `redis`/`disk`
backends pull their own optional extras only when selected). No HTML/markdown lib;
**no retry lib** (the bounded retry #4 is hand-rolled `asyncio`, not `tenacity`).
`dev` group: add **`respx`** (httpx mocking) and **`fakeredis`** (the
backend-agnostic cache test, doc 06 §06.5). Optionally `uvloop` (prod-only).
Regenerate `uv.lock`. Everything else (pydantic, fastmcp, rich, logdecorator, OTEL
extra) is already present.
