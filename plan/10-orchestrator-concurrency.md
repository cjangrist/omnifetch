# 10 — Orchestrator + concurrency (the engine core)

> The crown jewel. Source: `fetch_orchestrator.ts` (727). Split into:
> `waterfall.py` (config), `skip.py` (skip-providers parsing), `concurrency.py`
> (the race executors), `orchestrator.py` (`run_fetch_race`). Read
> `14-concurrency-performance.md` alongside this.
>
> `run_fetch_race` is ~250 LOC in TS and **must be decomposed** to satisfy
> RULE_09 #9 (≤45 lines/function).

---

## 10.1 `fetch/waterfall.py` — config as uppercase constants

Port `CONFIG` (`fetch_orchestrator.ts:104-153`) — **code is authoritative** over
`config.yaml` (overview §0.6).

```python
"""Waterfall topology: domain breakers + the tiered provider waterfall.

Pure data — the orchestrator walks BREAKERS first, then WATERFALL_STEPS top to
bottom. Mirrors the runtime CONFIG in fetch_orchestrator.ts (authoritative over
config.yaml).
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Breaker:
    name: str
    provider: str
    domains: tuple[str, ...]

# Iteration order matters (github → youtube → social_media), :105-123
BREAKERS: tuple[Breaker, ...] = (
    Breaker("github", "github",
            ("github.com", "gist.github.com", "raw.githubusercontent.com")),
    Breaker("youtube", "supadata", ("youtube.com", "youtu.be")),
    Breaker("social_media", "sociavault",
            ("tiktok.com", "instagram.com", "youtube.com", "youtu.be",
             "facebook.com", "fb.com", "twitter.com", "x.com", "pinterest.com",
             "reddit.com", "threads.net", "snapchat.com")),
)

# Step kinds: ("solo", "p")  ("parallel", ("a","b"))  ("sequential", (...))
WATERFALL_STEPS: tuple[tuple[str, object], ...] = (
    ("solo", "tavily"),
    ("solo", "firecrawl"),
    ("solo", "kimi"),
    ("parallel", ("linkup", "cloudflare_browser")),
    ("parallel", ("diffbot", "olostep")),
    ("parallel", ("scrapfly", "scrapedo", "decodo")),
    ("solo", "zyte"),
    ("solo", "brightdata"),
    ("sequential", ("jina", "spider", "you", "scrapeless", "scrapingbee",
                    "scrapegraphai", "scrappey", "scrapingant", "oxylabs",
                    "scraperapi", "leadmagic", "opengraph")),
)
```
Prefer a small `Step` union (typed) over the loose tuple if mypy complains; a
`@dataclass Step{kind, providers: tuple[str,...]}` is cleaner. (`serpapi` is
intentionally absent — explicit-only, overview §0.6.)

### `matches_breaker(url, breaker)` (`:200-209`)
```python
def matches_breaker(url: str, breaker: Breaker) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    host = host.lower().removeprefix("www.")
    return any(host == d or host.endswith(f".{d}") for d in breaker.domains)
```

---

## 10.2 `fetch/skip.py` — skip-providers parsing + validation

Port `parse_skip_providers` (`:402-449`) and `validate_skip_providers`
(`:456-465`). The LLM sends skip lists in many shapes; accept them all.

`parse_skip_providers(raw) -> list[str]`:
- `None` → `[]`.
- `list` → take first **64** (`MAX_ARRAY_ENTRIES`), keep `str` items ≤200 chars,
  `strip().lower()`, drop empties.
- non-`str` → `[]`. `str` > **4096** chars → `[]`.
- `"null"`/`"undefined"` (case-insensitive) → `[]`.
- starts with `[` → try `json.loads`; if a list, normalize items; on failure fall
  through to the regex strip-and-split.
- fallback: strip smart-quotes (`‘’“”`), strip surrounding `[]`/`"`/`'`, split on
  commas, `strip().lower()`, drop empties.

`validate_skip_providers(parsed, active_names) -> (valid, unknown)`: intersect
against the active provider names; unknowns are surfaced so callers can 400/error
(doc 11) rather than silently no-op a typo.

> Python note: the MCP tool input arrives already typed (FastMCP/Pydantic), so
> `raw` is usually `str | list[str] | None`. Keep the full parser anyway — it's the
> documented contract and is cheap; it also future-proofs the REST path.

---

## 10.3 `fetch/concurrency.py` — the race executors (the hard part)

A small mutable `RaceContext` threads the accumulators (mirrors the TS
`StepContext` + the race-level `attempted`/`failed`).

```python
@dataclass(slots=True)
class RaceContext:
    unified: UnifiedFetchProvider
    url: str
    active: set[str]
    attempted: list[str]
    failed: list[ProviderFailure]
    trace: TraceSink            # doc 12; no-op when tracing disabled
```

### `try_provider` (`:213-227`) — attempt + quality gate
```python
async def try_provider(ctx: RaceContext, provider: str) -> FetchResult:
    result = await ctx.unified.fetch_url(ctx.url, provider)
    if is_fetch_failure(result, provider):
        raise ProviderError(
            ErrorType.PROVIDER_ERROR,
            f"Blocked or empty ({len(result.content or '')} chars)", provider)
    return result
```

### `run_solo` (`:239-262`) — single attempt, **NOT_FOUND fast-fails**
```python
async def run_solo(ctx: RaceContext, provider: str) -> FetchResult | None:
    if provider not in ctx.active:
        return None
    ctx.attempted.append(provider)
    t0 = monotonic()
    ctx.trace.provider_start(provider, ctx.url)
    try:
        result = await try_provider(ctx, provider)
        ctx.trace.provider_complete(provider, result, ms_since(t0))
        return result
    except ProviderError as exc:
        ctx.failed.append(ProviderFailure(provider=provider, error=str(exc),
                                          duration_ms=ms_since(t0)))
        ctx.trace.provider_error(provider, str(exc), ms_since(t0))
        if exc.error_type is ErrorType.NOT_FOUND:   # definitively absent → fast-fail
            raise
        return None         # INVALID_INPUT / others → fall through to next provider
```
**Semantics to preserve exactly** (`:254-260`): `NOT_FOUND` (e.g. raw github 404)
re-raises → the race fast-fails. `INVALID_INPUT` (specialized provider can't
handle this URL type, e.g. sociavault on a non-social URL, github on
`compare/...`) returns `None` → fall through.

### `run_parallel` (`:264-325`) — multi-winner race with **loser cancellation**
This is the most subtle piece. TS launches all, collects up to `target_count`
winners, settles when reached or all complete, and **discards post-settle losers**
(the `resolved` flag suppresses their `ctx.failed`/trace mutations). Python
improves on it by **cancelling** the losing in-flight tasks (saves provider $ +
latency) while preserving the suppression semantics.

```python
async def run_parallel(ctx: RaceContext, providers: tuple[str, ...],
                       target_count: int) -> list[tuple[str, FetchResult]]:
    available = [p for p in providers if p in ctx.active]
    if not available:
        return []
    ctx.attempted.extend(available)

    starts: dict[asyncio.Task[FetchResult], tuple[str, float]] = {}
    for p in available:
        ctx.trace.provider_start(p, ctx.url)
        task = asyncio.create_task(try_provider(ctx, p))
        starts[task] = (p, monotonic())

    winners: list[tuple[str, FetchResult]] = []
    pending = set(starts)
    try:
        while pending and len(winners) < target_count:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                provider, t0 = starts[task]
                exc = task.exception()
                if exc is None and len(winners) < target_count:
                    result = task.result()
                    ctx.trace.provider_complete(provider, result, ms_since(t0))
                    winners.append((provider, result))
                elif exc is not None:
                    ctx.failed.append(ProviderFailure(
                        provider=provider, error=str(exc), duration_ms=ms_since(t0)))
                    ctx.trace.provider_error(provider, str(exc), ms_since(t0))
    finally:
        for task in pending:            # cancel + suppress the losers
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    return winners
```
**Faithful points:**
- Pre-settle successes beyond `target_count` are ignored; pre-settle failures are
  recorded; **post-settle (cancelled) tasks are suppressed** (their result/error
  never touch `ctx.failed`/trace) — exactly the `resolved`-guard behavior of
  `:305-318`.
- `available.length == 0 → []` (`:270`); all-failed logs "All parallel providers
  failed" (`:290-296`) — port the debug log.
- **Improvement over TS**: TS lets losers run to completion (results dropped);
  Python cancels them. This is strictly better for cost/latency and is safe because
  `httpx` requests cancel cleanly. Note this divergence in the code comment + 14.
- `try_provider` raising `NOT_FOUND` inside a parallel step is **recorded, not
  fast-failed** (parity: only `run_solo` re-raises NOT_FOUND).

### `run_sequential` (`:327-352`) — one-by-one until target
```python
async def run_sequential(ctx, providers, target_count) -> list[tuple[str, FetchResult]]:
    winners = []
    for provider in providers:
        if len(winners) >= target_count:
            break
        if provider not in ctx.active:
            continue
        ctx.attempted.append(provider)
        t0 = monotonic()
        ctx.trace.provider_start(provider, ctx.url)
        try:
            result = await try_provider(ctx, provider)
            ctx.trace.provider_complete(provider, result, ms_since(t0))
            winners.append((provider, result))
        except ProviderError as exc:
            ctx.failed.append(ProviderFailure(provider=provider, error=str(exc),
                                              duration_ms=ms_since(t0)))
            ctx.trace.provider_error(provider, str(exc), ms_since(t0))
    return winners
```
(Sequential does **not** re-raise NOT_FOUND — parity with `:344-349`.)

### `execute_step` (`:354-370`) — dispatch a step
`("solo", p)` → `run_solo` wrapped to `[(p, result)]`; `("parallel", ps)` →
`run_parallel`; `("sequential", ps)` → `run_sequential`.

---

## 10.4 `fetch/orchestrator.py` — `run_fetch_race`

The public entry. Decompose the TS mega-function into focused helpers.

```python
async def run_fetch_race(
    unified: UnifiedFetchProvider, url: str, *, cache: FetchCache,
    provider: str | None = None, skip_cache: bool = False,
    skip_providers: list[str] | None = None,
    is_grounding_internal: bool = False,
) -> FetchRaceResult:
```

**Control flow (mirror `:469-727`), as helpers:**

0. **Bind `request_context()`** (doc 12 §12.6) around the whole body so every
   provider / HTTP / decision log emitted for this fetch shares a fresh `request_id`
   — mirrors TS `run_with_request_id(crypto.randomUUID())` (`tools.ts:307`) and is
   the cheap correlation handle under concurrent calls (#9).
1. `_validate_skip(...)` → `effective_skip, has_skip` (drop unknowns + warn,
   `:487-494`).
2. **Cache read** — only when `provider is None and not skip_cache and not has_skip`
   (`:521-531`): `cached = await cache.get(url)`; hit → trace cache_hit,
   emit, **return cached**.
3. **Explicit-provider mode** (`provider is not None`, `:534-569`): one
   `unified.fetch_url(url, provider)`; on exception → trace+emit error, re-raise;
   if `is_fetch_failure` → raise `PROVIDER_ERROR "blocked or empty"`; else build +
   emit + **return** (no cache write in explicit mode).
4. **Auto waterfall** (`:571-702`):
   - `active = {n for n in unified.active_names if n not in skip_set}` (preserve
     registry order via a list-filter, then a set for membership) — `:573`.
   - `if not active`: raise `INVALID_INPUT` with the right reason (all-skipped vs
     no-keys, `:589-598`).
   - `target_count = min(2 if has_skip else 1, len(active))` (`:618`).
   - `_run_breakers(ctx, target_count)` then `_run_waterfall(ctx, target_count)`
     (below), inside a `try/except ProviderError` that **fast-fails on NOT_FOUND**
     (`:667-677`: trace `waterfall_fast_fail`, flush, emit `fast_fail`, re-raise).
   - winners → `primary = winners[0]`; `race = _build_result(...)`;
     `if not has_skip: await cache.set(url, race)` (`:604-610`);
     `race.alternative_results = [AlternativeResult(provider=p, result=r) for p,r
     in winners[1:]]` when >1 (`:696-698`); trace+emit resolved; **return**.
   - no winners → trace `waterfall_exhausted`, emit `exhausted`, raise
     `PROVIDER_ERROR f"All providers failed for {url[:200]}. Tried: {', '.join(attempted)}"`
     (`:721-725`).

**`_run_breakers`** (`:622-652`): iterate `BREAKERS` in order; stop if
`len(winners) >= target_count`; `if matches_breaker(url, b)`: if `b.provider not in
active` → trace `breaker_skipped` (reason in_skip_set vs inactive), continue; else
trace `breaker_match`, `r = await run_solo(ctx, b.provider)`; `r` → winner +
`breaker_resolved`; else `breaker_fallthrough`. (run_solo here means a breaker's
NOT_FOUND fast-fails the whole race.)

**`_run_waterfall`** (`:654-666`): for each `step` in `WATERFALL_STEPS`: stop if
target met; `remaining = target_count - len(winners)`; `step_winners = await
execute_step(ctx, step, remaining)`; extend winners (capped at target).

### Decomposition checklist (each ≤45 lines)
`run_fetch_race` (orchestration only) · `_validate_skip` · `_try_cache` ·
`_run_explicit` · `_run_auto` · `_run_breakers` · `_run_waterfall` ·
`_build_result` · `_finish_success` · `_emit` (metric closure → a small callable
or a tiny class, doc 12).

---

## 10.5 Concurrency/perf properties (summary; full rationale in 14)
- **Bounded fan-out**: at most `len(step.providers)` providers run concurrently
  (≤3 in the configured waterfall). With `target_count=1`, a parallel step returns
  on the **first** success and cancels the rest → tail-latency win.
- **No global lock**: `RaceContext` is touched only from the single orchestrating
  coroutine (the race awaits results sequentially in the `asyncio.wait` loop), so
  `attempted`/`failed`/`winners` need no lock. The provider tasks do I/O only.
- **Cancellation correctness**: losers are cancelled in `finally`; we `await
  gather(..., return_exceptions=True)` so cancellation fully settles before the
  step returns (no orphaned tasks, no "task was destroyed but pending" warnings).
- **Per-provider deadline** = each provider's httpx timeout (doc 02) + an outer
  `provider_timeout` for non-httpx work (supadata poll). No global race deadline
  (parity); add one only if you want a hard SLA (14).

---

## 10.6 Acceptance criteria
Use **fake providers** (a `UnifiedFetchProvider` test double whose `fetch_url`
returns/raises on a schedule, with injectable delays) — no network.

1. **First-success solo**: `[fail(50 chars), ok(500)]` as steps → result from the
   second; first appears in `providers_failed`; `providers_attempted == [p0, p1]`.
2. **Parallel first-winner + cancellation**: a parallel step `[slow_ok(200ms),
   fast_ok(20ms)]`, `target_count=1` → returns `fast_ok` in ~20ms and **cancels**
   `slow_ok` (assert the slow provider's `fetch_url` was cancelled / its full work
   didn't complete; assert it is NOT in `providers_failed`).
3. **Multi-winner (skip path)**: `target_count=2` over a 3-provider parallel step
   with 2 successes → `winners == 2`, `alternative_results` has the 2nd; cache
   **not** written (has_skip).
4. **Breaker routing**: `youtube.com/watch?v=x` → tries `supadata` first
   (breaker), and on its failure continues to `social_media`→`sociavault`, then the
   waterfall; `github.com/o/r` → `github` breaker first.
5. **NOT_FOUND fast-fail**: a solo/breaker provider raising `NOT_FOUND` aborts the
   race immediately (subsequent steps **not** attempted); the same `NOT_FOUND`
   inside a parallel/sequential step is merely recorded and the race continues.
6. **INVALID_INPUT fall-through**: a breaker provider raising `INVALID_INPUT`
   (e.g. sociavault on non-social URL) falls through to the next provider.
7. **Empty active set**: all providers skipped → `ProviderError(INVALID_INPUT,
   "No fetch providers available — all candidates skipped...")`; no keys → "...no
   providers configured with API keys".
8. **Cache**: cold call writes; warm call returns the cached `FetchRaceResult`
   without invoking any provider; `skip_cache=True` and any `skip_providers` bypass
   read **and** write.
9. **Exhaustion**: every provider fails → `ProviderError(PROVIDER_ERROR, "All
   providers failed for <url>. Tried: <attempted>")`.
10. **skip parsing**: `'["tavily","firecrawl"]'`, `"tavily, firecrawl"`,
    `["tavily"]`, `"null"`, smart-quoted `"[tavily]"` all parse per `:402-449`;
    unknown names surfaced by `validate_skip_providers`.
11. **Determinism**: `providers_attempted` order matches the waterfall/registry
    order across runs.
12. `mypy --strict` + ruff clean; no function > 45 lines; no orphaned-task warnings
    under `pytest -W error`.

## 10.7 Interfaces
**Exposes:** `run_fetch_race`, `parse_skip_providers`, `validate_skip_providers`,
`BREAKERS`, `WATERFALL_STEPS`, `matches_breaker`, and the `concurrency` executors
(for tests). **Consumes:** `fetch/registry` (`UnifiedFetchProvider`),
`fetch/failure`, `fetch/cache`, `fetch/types`, `fetch/observability`, `fetch/util`.
