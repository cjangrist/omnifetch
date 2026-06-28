# Critical Comparison: `plan-claude/` vs. `plan-gemini/`

> Subject: two independently-written implementation plans for porting the
> **URL‑fetch** functionality of `omnisearch` (TS / Cloudflare Workers MCP) into
> `omnifetch` (Python / FastMCP). Scope = the multi-provider waterfall over ~28
> scraping APIs with failover, domain breakers, a 36‑hour cache, and an asyncio
> concurrency model.
>
> Both plan directories were read in full before writing this document
> (`plan-claude/`: 15 files, ~2,800 lines; `plan-gemini/`: 7 files, ~210 lines).
> Key fidelity claims were spot-checked against `omnisearch/src/` and the
> `omnifetch/` scaffold.

---

## 1) High-level overview of differences

### Structure & decomposition

**Claude** ships **15 numbered work-packages** organized as a dependency DAG
(`plan-claude/00-overview.md` §0.7), each a self-contained doc with its own
*Design → Decisions → Acceptance criteria → Interfaces (exposes/consumes)*
sections:

- `00-overview.md` — master plan, source inventory, TS→Python primitive mapping,
  target conventions, the authoritative waterfall (§0.6), build DAG, project DoD.
- `01-foundations.md` (types/html/util), `02-async-http-client.md`,
  `04-provider-config.md`, `05-failure-detection.md`, `06-cache.md`.
- `07-providers-base-registry-generic.md` (19 providers in one table),
  `08-providers-structured.md` (zyte/diffbot/opengraph/scrappey),
  `09-providers-specialized.md` (supadata/serpapi/sociavault/kimi),
  `09b-provider-github.md` (the ~2,000-LOC GitHub subpackage).
- `10-orchestrator-concurrency.md` (the engine core, decomposed),
  `11-mcp-tool-server.md`, `12-observability.md`, `13-testing-and-parity.md`,
  `14-concurrency-and-performance.md` (a dedicated cross-cutting design doc).

The decomposition maps almost 1:1 to a proposed Python module tree
(`00-overview.md` §0.4) and explicitly splits the 727-line TS
`fetch_orchestrator.ts` into `waterfall.py + skip.py + concurrency.py +
orchestrator.py + cache.py + failure.py` to honor the project's ≤45-line /
≤500-line rules.

**Gemini** ships **7 "Area" documents** organized by architectural layer, each
~25–37 lines with a uniform *Objective → Code References → Implementation Plan →
Acceptance Criteria* shape:

- `01-core-http-client.md`, `02-core-types-and-config.md`,
  `03-provider-framework.md`, `04-provider-implementations.md`,
  `05-api-and-entrypoint.md`, `06-observability-and-tracing.md`,
  `07-caching-and-auth.md`.

It reads as a tidy **architectural sketch / RFC**, not a build manual. There is
no module tree, no build order, no dependency graph, and — critically — **no
document for the orchestrator/waterfall itself** (the thing the task calls "the
core"). The closest mention is an *optional* `fetch_any` race in
`plan-gemini/03-provider-framework.md` step 4.

### Depth & coverage

| Subsystem | Claude | Gemini |
|---|---|---|
| Waterfall topology (9 tiers, solo/parallel/sequential) | Fully specified, verified vs. source (`10` §10.1, `00` §0.6) | **Absent** |
| Domain breakers (github/youtube/social_media) | Fully specified incl. iteration order (`10` §10.1) | **Absent** |
| Failure/quality gate (`is_fetch_failure`, `detect_grounded_junk`, 11 challenge + 28 TIGHT + 9 AMBIGUOUS patterns, API-native bypass) | Entire doc `05` | **Absent** |
| `skip_providers` parsing + `target_count`/`alternative_results` 2-provider compare | `10` §10.2 / §10.4 | **Absent** |
| Multi-winner race + loser cancellation + fast-fail vs. fall-through | `10` §10.3, `14` §14.2 | Optional `as_completed` one-liner (`03` step 4) |
| Per-provider request/response mapping | All 28 enumerated in tables (`07`/`08`/`09`/`09b`) | **One** example (Jina, `04`) |
| GitHub provider (~2,000 LOC, URL parser, GraphQL, dispatch) | Dedicated doc `09b` | Folded into "remaining 27 providers in batches" (`04`) |
| Cache (36h TTL, URL-guard, corrupt-entry-is-miss) | Doc `06`, TTL = 129,600s | Redis, TTL unspecified (`07`) |
| Concurrency/perf rationale | Dedicated doc `14` | Scattered one-liners |

### Scope & emphasis

- **Claude** matches the task's stated emphasis order (concurrency → performance →
  hierarchical modules) and the user's FP-waiver verbatim (`00` §0.3), explicitly
  marks search/answer/RRF **out of scope**, and treats security as second-order
  (`00` header; `11` §11.5).
- **Gemini** re-frames the project as a standalone **FastAPI microservice** with
  Redis, S3/MinIO forensic storage, Prometheus, and enforced Bearer auth
  (`05`, `06`, `07`). It emphasizes *infrastructure and deployment* over the fetch
  engine, and elevates security (auth) to a first-class requirement despite the
  task de-prioritizing it.

### Style

- **Claude**: dense, citation-heavy (every claim carries a `file.ts:line`
  reference), prescriptive code skeletons, per-package acceptance criteria, and an
  explicit "divergences from TS that are net-positive" ledger (`14` §14.9).
  Cost: ~2,800 lines — a lot to read.
- **Gemini**: concise, skimmable, layer-oriented; you can grasp the intended shape
  in five minutes. Cost: almost none of the actual fetch behavior is pinned down,
  and several choices silently contradict the target.

---

## 2) Which is better, and why

**Verdict: `plan-claude/` is substantially better — not marginally, but by a wide
margin** — on every dimension that matters for *this* port. I went in prepared to
credit Gemini if its concision hid superior judgment; it does not. Gemini's plan
is a plausible-sounding sketch for a *different* project (a greenfield FastAPI
scraping microservice) and omits the engine that the task is explicitly about.

### Dimension-by-dimension

| Dimension | Claude | Gemini | Notes |
|---|:---:|:---:|---|
| Completeness / scope coverage | 5 | 2 | Gemini omits the waterfall, breakers, failure gate, skip-providers, and 27 of 28 provider mappings. |
| Correctness & fidelity to TS source | 5 | 2 | Claude verified against source (see below). Gemini never captures failover semantics; introduces likely errors. |
| Fidelity to the **target** (`omnifetch` FastMCP scaffold) | 5 | 1 | Gemini targets FastAPI/uvicorn/Redis/S3/Prometheus; target is FastMCP/OTEL/in-memory with a 100% coverage gate. |
| Concurrency design | 5 | 2 | Claude: multi-winner race, loser cancellation, `wait(FIRST_COMPLETED)` loop, fast-fail vs. fall-through, lock-free `RaceContext`. Gemini: an optional `as_completed`. |
| Performance design | 5 | 2.5 | Claude: pool math, tail-latency, CPU-on-loop analysis, 5MB stream cap, cancellation savings. Gemini: pooling + `limit_per_host` (a real plus) but little else. |
| Module hierarchy / decomposition | 5 | 3 | Claude maps to a full module tree and splits the 727-line orchestrator. Gemini's `core/providers/api` split is reasonable but coarse and never decomposes the engine (it has none). |
| Testability & acceptance criteria | 5 | 2.5 | Both have per-unit checklists. Claude pins the 100% branch gate, the in-memory `Client`, fake-provider concurrency tests, cross-impl hash vectors, and a TS-parity corpus. Gemini ignores the existing harness + coverage gate. |
| Clarity / readability | 4 | 4 | Gemini is more skimmable as an orientation; Claude is denser but far more precise and navigable section-to-section. Roughly even. |
| Actionability / execute-in-isolation | 5 | 2 | An engineer can build Claude's plan module-by-module against its acceptance tests. Gemini leaves the core engine, 27 providers, and the failover rules unspecified. |

**Why the fidelity gap is decisive.** I verified Claude's central claims directly:

- The waterfall in `fetch_orchestrator.ts` is **exactly** Claude's §0.6 — including
  the easy-to-miss `{ solo: 'kimi' }` at step 3, the `parallel: ['scrapfly',
  'scrapedo', 'decodo']` trio, and the 12-provider `sequential` tail.
- The three breakers (`github→github`, `youtube→supadata`,
  `social_media→sociavault`) and their domain lists match `10` §10.1 verbatim.
- The 11 `challenge_patterns`, the `API_NATIVE_PROVIDERS = {github, supadata}`
  bypass, and the `is_fetch_failure` ordering match doc `05` line-for-line. The
  source even carries the cache-poisoning comment that Claude reproduces as
  rationale in `05`/`06`.
- The registry has exactly **28** providers (`unified/fetch.ts:41-70`); `serpapi`
  is registered but never auto-selected — the "explicit-only" subtlety Claude flags
  in `00` §0.6 and `09` §9.2.
- `FetchRaceResult`'s shape matches doc `06` field-for-field.

Gemini's plan contains **none** of this. It never enumerates the waterfall, never
mentions breakers, never mentions the failure gate, and gives one provider example
for all 28. The task's one-sentence description ("a multi-provider waterfall …
with automatic failover, domain breakers, a 36-hour cache, and an asyncio
concurrency model") names five subsystems; Gemini specifies the cache and (partly)
the concurrency, and is silent on the other three.

**Where Gemini actively diverges from the target (not just the source):**

- **Wrong framework.** `omnifetch/pyproject.toml` and `README.md` describe a
  **FastMCP** server (stdio/HTTP MCP transport, `fastmcp==3.4.2`). Gemini's
  `05-api-and-entrypoint.md` builds a **FastAPI/uvicorn** REST service and only
  mentions "or an MCP server" in passing — it never addresses the
  `register_<tool>_tool` pattern, `tools/__init__.py::_REGISTRARS`, the in-memory
  `Client` test harness, `logdecorator` entry/exit logging, or the existing
  `server.py`/`schemas.py`/`config.py`/`telemetry.py` scaffold. Claude builds on
  all of them (`11`, `13`).
- **Net-new infrastructure not in source or target.** Gemini mandates Redis
  (`07`), S3/MinIO + `aiobotocore` (`06`), and Prometheus `/metrics` (`06`). The
  target's only telemetry is opt-in **OpenTelemetry** (`telemetry.py`); its cache
  requirement is satisfiable in-process. Claude maps KV→in-memory TTL with a
  swappable `CacheBackend` ABC and R2/AnalyticsEngine→OTEL (`06`, `12`) — far
  closer to the scaffold's zero-extra-infra ethos.
- **Likely correctness bugs.** Gemini's `02` proposes provider keys under an
  `OMNIFETCH_JINA_API_KEY` env prefix; the source and the target's `.env.example`
  use provider-native names (`JINA_API_KEY`), which Claude preserves deliberately
  for drop-in `.env` compatibility (`04` §04.2). Gemini's base method returns
  `dict` (`03`) while its Jina example returns `FetchResult` (`04`) — an unresolved
  contract inconsistency. Its `01` puts automatic `tenacity` retries on 429/5xx
  *inside* the HTTP client, which would fight the orchestrator's failover model
  (in the source a 429 raises `RATE_LIMIT` and the waterfall moves on).

**Net:** Claude's plan is executable, faithful, and complete; Gemini's is a
readable but under-specified sketch aimed at the wrong runtime. Claude wins.

---

## 3) Improvements Gemini's plan has that Claude's does not

Being genuinely critical of Claude: Gemini does include several legitimate ideas,
defaults, or framings that Claude either omits, defers, or dismisses. Each is
listed with its exact location in Gemini and the corresponding gap in Claude. (A
few carry caveats — noted honestly — but all are real.)

1. **Explicit per-host connection cap.**
   `plan-gemini/01-core-http-client.md` step 1 sets
   `TCPConnector(limit=100, limit_per_host=20)` — a concrete guard against a single
   slow/duplicated host (e.g. kimi routing through Scrapfly *while* a parallel step
   also hits Scrapfly) monopolizing the pool.
   *Claude gap:* `02` §02.2 and `14` §14.4 set only a global
   `max_connections=100`/`max_keepalive_connections=40` and merely *discuss*
   per-host saturation qualitatively ("minor; the pool absorbs it") without an
   enforced per-host bound. (httpx has no native per-host limit, which makes the
   omission more notable, not less.)

2. **A concrete persistent / shared cache backend (Redis).**
   `plan-gemini/07-caching-and-auth.md` step 1 commits to `redis.asyncio` as the
   cache, giving restart-survival and cross-replica sharing for a "production-grade"
   deployment.
   *Claude gap:* `06` §06.3 anticipates this ("a later `RedisCache(CacheBackend)`
   … drops in with zero orchestrator changes") but **defers it as out-of-scope**
   and ships only `InMemoryTTLCache`. Gemini provides the default Claude only
   gestures at. (Caveat: in-memory is the *correct* default for the actual
   single-process stdio MCP target; this is an advantage only for a scaled HTTP
   deployment.)

3. **Full-fidelity, durable forensic trace storage with schema parity.**
   `plan-gemini/06-observability-and-tracing.md` steps 2 + acceptance criteria
   preserve the R2 forensic pipeline by writing the **full** trace JSON to S3 via
   `aiobotocore`, and explicitly require "Trace payloads identically match the JSON
   schema from `r2_trace.ts` (trace_id, tool, providers_hit, http_calls)."
   *Claude gap:* `12` §12.1 **downgrades** the R2 blob to an *optional,
   off-by-default, redacted local* JSON dump and leans on OTEL spans instead.
   Gemini retains more of that one subsystem's parity (durable, complete,
   schema-matching forensics). (Caveat: Claude's redaction/gating is the more
   security-sound default; the two make different, defensible tradeoffs.)

4. **A bounded transient-failure retry with backoff.**
   `plan-gemini/01-core-http-client.md` step 4 wraps HTTP calls with `tenacity`
   exponential backoff for transient 429/5xx.
   *Claude gap:* `00` §0.5 and `01` §01.3 call retry **"optional … not on the
   critical path"** and note no provider uses it. A *single, bounded* retry on a
   transient 5xx/network blip *before* failover is a legitimate resilience idea
   Claude dismisses entirely. (Caveat: it must not retry `RATE_LIMIT`/`API_ERROR`
   and must stay inside the per-provider timeout, or it becomes a fidelity
   regression — see §4.)

5. **An explicit phased / incremental rollout (MVP vertical slice).**
   `plan-gemini/04-provider-implementations.md` step 3 defines "Phase 1: port the
   top 5 critical providers (Jina, Firecrawl, Tavily, Zyte, ScraperAPI); Phase 2:
   the rest in batches" — i.e. get an end-to-end working slice early.
   *Claude gap:* `00` §0.7 gives a dependency DAG and build *order*, but no
   "ship a thin working subset first" milestone. The whole engine + all 28
   providers + GitHub are implicitly a big-bang before anything runs end-to-end.

6. **First-class, enforced, timing-safe Bearer auth.**
   `plan-gemini/07-caching-and-auth.md` step 2 + acceptance criteria port
   `authenticate_rest_request` as a FastAPI `Depends`, use `secrets.compare_digest`,
   and **require** 401-in-constant-time on the endpoint.
   *Claude gap:* `11` §11.5 marks REST auth a "**stretch goal** … can be
   unauthenticated," and `00` declares security second-order. Gemini gives a
   concrete, wired auth port. (Caveat: the user explicitly de-prioritized security
   and the primary surface is MCP stdio, so this matters less here — but it *is*
   covered more concretely.)

7. **External, real-world load-testing as an acceptance criterion.**
   `plan-gemini/05-api-and-entrypoint.md` acceptance criteria require load testing
   "using `locust` or `k6` … thousands of concurrent fetch requests."
   *Claude gap:* `14` §14.8 / `13` §13.3 validate concurrency *in-process* (fake
   providers, the in-memory `Client`, microbenches). Gemini additionally asks for
   an out-of-process load test against the served transport — a more realistic
   production-scale check.

8. **A pull-based Prometheus `/metrics` surface.**
   `plan-gemini/06-observability-and-tracing.md` step 3 exposes a `/metrics`
   endpoint via `prometheus_client`.
   *Claude gap:* `12` §12.2 emits OTEL instruments + a structured log but never
   spells out an always-on scrape endpoint. (Caveat: OTEL is the target's chosen
   stack and can export to Prometheus; this is a packaging preference, but Gemini's
   concrete scrape surface is ops-familiar and Claude leaves it implicit.)

9. **Skimmability / fast architectural orientation.**
   The 7 short Gemini Areas convey the intended layering at a glance.
   *Claude gap:* `00-overview.md` is thorough but long; there is no one-screen
   "architecture at a glance" summary for a reader who just wants the shape.

---

## 4) Changes required to the Claude plan to fix #3

Concrete, executable edits. Each names the Claude file + section and states exactly
what to add or change so the plan absorbs every advantage from §3 **without losing
its fidelity to the FastMCP/OTEL/in-memory target**.

- [ ] **(#1 per-host cap)** Edit `plan-claude/14-concurrency-and-performance.md`
      §14.4 and `plan-claude/02-async-http-client.md` §02.2. Add an enforced
      per-host concurrency bound. Because `httpx.Limits` has no per-host knob, add
      an `asyncio.Semaphore`-per-host (or per-provider) gate in `http.py` keyed on
      `urlsplit(url).hostname`, default 20, exposed as
      `OMNIFETCH_HTTP_LIMIT_PER_HOST`. Add an acceptance test: 50 concurrent calls
      to one host never exceed the cap. Note the divergence from aiohttp's native
      `limit_per_host` in the code comment.

- [ ] **(#2 Redis cache)** Edit `plan-claude/06-cache.md` §06.3. Promote the
      deferred `RedisCache(CacheBackend)` from a one-line "future note" to a
      concrete optional implementation sketch: `redis.asyncio` client,
      `get`/`set` storing `model_dump_json()`, the same `hash_key("fetch:", url)`
      keys (reuse the cross-impl vector from `13` §13.4 so keys are byte-identical
      to TS), TTL = 129,600s. Add a config switch
      `OMNIFETCH_CACHE_BACKEND=memory|redis` + `OMNIFETCH_REDIS_URL` (wired in `04`
      §04.2 / `11` §11.4 lifespan). Keep `InMemoryTTLCache` the default. Add an
      acceptance criterion that the orchestrator is unchanged across backends.

- [ ] **(#3 durable forensic trace)** Edit `plan-claude/12-observability.md` §12.1
      (the `flush_background → R2` row) and §12.5. Make the full-fidelity JSON dump
      a first-class, **schema-versioned** artifact whose fields mirror
      `r2_trace.ts` (`trace_id`, `tool`, `providers_hit`, `http_calls`, …), behind
      a pluggable `TraceDumpSink` with two impls: local-disk (default) and optional
      S3 (`aiobotocore`), gated by `OMNIFETCH_TRACE_DUMP_DIR` /
      `OMNIFETCH_TRACE_DUMP_S3`. Keep redaction via `http._redact`. Add an
      acceptance criterion: a dumped trace validates against a pinned JSON schema
      matching `r2_trace.ts` (parity), and contains no API keys.

- [ ] **(#4 bounded retry)** Edit `plan-claude/02-async-http-client.md` §02.2 and
      `plan-claude/14-concurrency-and-performance.md` §14.3. Add an **optional,
      config-gated, single** retry path in `_request`: retry **only**
      `ProviderError(PROVIDER_ERROR)` (5xx/network) — never `RATE_LIMIT`,
      `API_ERROR`, `INVALID_INPUT`, `NOT_FOUND` — with one short backoff, the total
      kept strictly inside `provider_timeout`. Default
      `OMNIFETCH_HTTP_TRANSIENT_RETRIES=0` (strict TS parity); document that >0
      trades a small latency cost for blip-resilience and must not subvert the
      waterfall. Add a §02.5 acceptance test for "5xx retried once then failover;
      429 not retried."

- [ ] **(#5 phased rollout)** Edit `plan-claude/00-overview.md` §0.7 (sequencing)
      and §0.8 (DoD). Insert an explicit **"M1: vertical slice"** milestone — build
      `types/util/http/config` + a minimal `registry` with ~5 providers (tavily,
      firecrawl, jina, zyte, scraperapi) + `failure` + `cache` + the
      `orchestrator` + the `fetch` tool, and prove one end-to-end `fetch()` against
      mocked providers — **before** fanning out the remaining 23 providers and
      GitHub (`09b`). Re-state §0.8's DoD as staged milestones (M1 slice → M2 all
      generic/structured/specialized → M3 GitHub → M4 100% coverage + parity).

- [ ] **(#6 enforced auth)** Edit `plan-claude/11-mcp-tool-server.md` §11.5.
      Upgrade the optional REST `/fetch` bearer-auth from "stretch / can be
      unauthenticated" to a concrete port of `authenticate_rest_request` using
      `hmac.compare_digest` (already noted in `01` §01.3 / `00` §0.5), gated by
      `OMNIFETCH_REST_BEARER_TOKEN`, returning 401 in constant time, with an
      acceptance criterion. Keep it **optional** (MCP stdio remains the primary
      surface) but specify it fully so REST parity is turnkey when enabled.

- [ ] **(#7 external load test)** Edit `plan-claude/13-testing-and-parity.md`
      §13.3/§13.7 and `plan-claude/14-concurrency-and-performance.md` §14.8. Add an
      out-of-process load/soak acceptance criterion against the **HTTP transport**
      (`--transport http`) with upstreams mocked at the network edge (e.g. a
      `locust`/`k6` script or an `asyncio.gather` driver of N clients), asserting
      flat latency and no pool errors up to the §14.4 concurrency target — to
      complement the existing in-process microbench.

- [ ] **(#8 Prometheus surface)** Edit `plan-claude/12-observability.md` §12.2/§12.3.
      Document an option to expose OTEL metrics via the OpenTelemetry **Prometheus
      exporter** and mount a `/metrics` scrape endpoint when running
      `--transport http`, gated by an env flag (e.g.
      `OMNIFETCH_PROMETHEUS_METRICS=1`). Keep OTEL the primary stack; present
      Prometheus as a supported export target rather than a parallel system.

- [ ] **(#9 skimmability)** Edit `plan-claude/00-overview.md`. Add a one-screen
      "Architecture at a glance" box near the top (≤25 lines): the layer stack
      (tool → orchestrator → registry → providers → http/cache/failure/observability)
      and the request lifecycle (cache → breakers → waterfall tiers → quality gate
      → cache write), so a new reader gets Gemini-level orientation before diving
      into the 15 packages.

---

### Bottom line

Claude's plan is the one to execute. Its only genuine deficits relative to Gemini
are *additive* and *optional* (per-host caps, a Redis backend, durable forensic
dumps, a bounded retry, a phased-rollout framing, concrete auth, external load
tests, a Prometheus surface, and a skimmable summary) — every one of which can be
folded in via the §4 checklist **without** importing Gemini's core mistakes
(FastAPI-instead-of-FastMCP, mandatory Redis/S3/Prometheus infrastructure, the
unspecified engine, and the single-example provider coverage).
