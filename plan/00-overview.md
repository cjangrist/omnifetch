# 00 — Overview & Master Plan: omnisearch (TS) → omnifetch (Python) fetch port

> Scope: **fetching functionality only**. Search (`web_search`), AI answer
> (`answer`), RRF ranking, grounded snippets, and the `/researcher` endpoint are
> **out of scope** and are NOT ported.
>
> Emphasis order (per request): **(1) concurrency, (2) performance, (3)
> hierarchical modules + shared tools.** Security is second-order — do the
> obvious-correct thing (don't log secrets, mask error details, which FastMCP
> already does) but do not invest design effort there yet.

---

## 0.1 What we are porting

`omnisearch` is a Cloudflare-Workers MCP server. Its `fetch` capability takes a
URL and returns clean markdown by running a **tiered provider waterfall** across
28 commercial scraping/extraction APIs, with automatic failover, domain-specific
"breakers", a 36-hour cache, and per-request tracing/metrics.

The fetch capability is surfaced two ways in the source:

| Surface | Source | Port? |
|---|---|---|
| MCP `fetch` tool | `omnisearch/src/server/tools.ts:254-350` (`register_fetch_tool`) | **Yes** |
| REST `POST /fetch` | `omnisearch/src/server/rest_fetch.ts` (whole file) | **Yes** — lightweight, second-order to the MCP tool, and easily toggled off (`11` §11.5) |

Both call the single engine entry point `run_fetch_race`
(`omnisearch/src/server/fetch_orchestrator.ts:469-727`).

`omnifetch` is a **FastMCP** (Python 3.11+, `uv`-managed) server. It currently
ships only a scaffold + a `say_hello` demo tool. This plan adds a real `fetch`
tool backed by a faithful, idiomatic-async re-implementation of the engine.

**Deployment posture — cloud-agnostic & vendor-neutral.** Where `omnisearch` is
welded to Cloudflare (Workers isolates, KV, R2, Analytics Engine, Durable
Objects), `omnifetch` is a plain long-lived Python process with **zero
cloud-provider dependency**: stdio **or** streamable-HTTP MCP transport, an
in-process (or Redis/Disk) cache via `py-key-value`, and OpenTelemetry → any OTLP
backend for traces/metrics. It runs identically on a laptop, a bare VM, any
container runtime, or any cloud, and ships a Docker + docker-compose setup (doc
15). Every Cloudflare→Python re-targeting in this plan is chosen to **avoid
vendor lock-in** — call this out when a decision could accidentally reintroduce it.

---

## 0.2 Source inventory (the exact surface to port)

All paths below are under `omnisearch/src/`. Line counts are the porting budget.

**Engine / orchestration**
- `server/fetch_orchestrator.ts` — 727 lines — waterfall, breakers, race, cache, skip-providers, failure gate, trace/metric emission. **The core.**
- `server/rest_fetch.ts` — 164 lines — REST entry (validation + auth + dispatch).
- `server/tools.ts:254-350` — MCP `fetch` tool registration + I/O schema.
- `providers/unified/fetch.ts` — 107 lines — provider registry array + `UnifiedFetchProvider` dispatcher.
- `providers/index.ts` — 110 lines — provider init / availability gating (idempotent).

**Shared "common" tools (only the parts the fetch path uses)**
- `common/types.ts` — 61 lines — `FetchResult`, `FetchProvider`, `ErrorType`, `ProviderError`.
- `common/http.ts` — 187 lines — `http_json` / `http_text`, 5 MB stream guard, status→error mapping.
- `common/utils.ts` — 167 lines — `validate_api_key`, `hash_key`, `handle_provider_error`, `handle_rate_limit`, `make_signal`, `retry_with_backoff`, `sanitize_for_log`, `timing_safe_equal`, `authenticate_rest_request`.
- `common/html.ts` — 14 lines — `extract_html_title`, `extract_markdown_title`.
- `common/r2_trace.ts` — 294 lines — per-request forensic trace (Cloudflare R2). Re-targeted to OpenTelemetry — see `12-observability.md`.
- `common/metrics.ts` — 151 lines — Workers Analytics Engine scalars. Re-targeted to OTEL metrics / structured logs — see `12-observability.md`.

**Failure heuristics**
- `server/grounded_prompts.ts:112-197` — `detect_grounded_junk` + TIGHT/AMBIGUOUS pattern lists (reused by the fetch failure gate). The rest of that file (snippet prompt) is **out of scope**.

**Config**
- `config/env.ts:136-283` — `config.fetch.*` (api_key / base_url / timeout per provider).
- `config/env.ts:403-437` — env-var → provider-key wiring.
- `.env.example:50-79` — the authoritative env var **names**.
- `config.yaml` — documentation mirror of the waterfall (DRIFTED — see §0.6).

**Providers** — `providers/fetch/<name>/index.ts`, 28 total (plus the `github/`
subpackage of 11 files). Catalogued in `07/08/09`.

---

## 0.3 Target conventions (omnifetch — non-negotiable)

Derived from `omnifetch/pyproject.toml`, `README.md`, and the existing scaffold.

- **FastMCP** server (`fastmcp==3.4.2`); tools are `async def` returning a
  Pydantic model, registered via a `register_<tool>_tool(server)` helper appended
  to `tools/__init__.py::_REGISTRARS` (pattern: `tools/hello.py`).
- **Typing**: passes `mypy --strict` (`disallow_any_generics`, `warn_unreachable`,
  etc.). Every function fully annotated.
- **Lint/style**: `ruff` with `E,F,I,N,UP,B,A,C4,SIM,TID,RUF,ANN,PL,D`, **Google
  docstring convention**, **80-column** lines, first-party `omnifetch`.
- **Logging**: colorized Rich → **stderr** (`omnifetch/logging.py`); `get_logger("fetch.<x>")`.
  Tool entry/exit logged via `logdecorator` (`@async_log_on_start/_on_end`) — and
  it MUST NOT log return values (the hello test asserts this:
  `tests/test_hello_tool.py:72-81`).
- **Config**: `pydantic-settings`, frozen, `OMNIFETCH_` prefix for server settings;
  `.env` via `python-dotenv` loaded once in `__main__.main()`.
- **Telemetry**: opt-in OpenTelemetry, zero-overhead no-op by default
  (`omnifetch/telemetry.py`).
- **Tests**: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), in-memory FastMCP
  `Client`, **`fail_under = 100`** branch coverage.

### CLAUDE.md directives that apply (and the one that does NOT)

The user explicitly **waived the functional-programming directive**
(`~/.claude/CLAUDE.md` RULE_09 #6 "prefer functions over classes" and #8 "all
pure functions"). **Classes/ABCs are therefore used** to mirror the provider
architecture — this is the natural and correct mapping.

Everything else in RULE_09 still binds and shapes this plan:
- **#1 Logging**: verbose, colorized, log entry/exit + params (not large returns).
- **#2 Config**: centralized uppercase module constants, `dotenv`-loaded.
- **#4 Header comment** per module (purpose + brief architecture).
- **#5 No inline comments**; verbose, non-abbreviated names. (Docstrings + block
  comments OK; ruff `D` enforces docstrings.)
- **#7 Primitive-typed signatures** where reasonable (but Pydantic models for wire
  contracts — FastMCP requires this).
- **#9 ≤30–45 lines/function.** Several TS functions exceed this (notably
  `run_fetch_race` ≈ 250 lines) and **must be decomposed** on the way over.
- **#10 Comprehensions/generators over loops** where it doesn't fight asyncio.
- **#11 >500 lines/file → split.** `fetch_orchestrator.ts` (727) is split into
  `orchestrator.py` + `concurrency.py` + `skip.py` + `failure.py` + `cache.py`.

Also: **never `rm`** (RULE_07 — cleanup to `trash/`); year is **2026**;
`uv`-managed project (RULE_06's "Anaconda base" is overridden by the explicit
project-level `pyproject.toml` + `uv.lock`).

---

## 0.4 Target module hierarchy (the proposed layout)

New code lives in a `fetch/` engine package + one new tool module. Existing
scaffold files are **extended, not replaced** (RULE_12: prefer editing).

```
omnifetch/                       repo root
  Dockerfile                     (NEW) — multi-stage uv build              (15)
  docker-compose.yml             (NEW) — stdio/http service + optional redis(15)
  .dockerignore                  (NEW)                                     (15)
  pyproject.toml                 (exists) — EXTEND deps: httpx, py-key-value-aio,
                                            tenacity, uvloop
  src/omnifetch/
    __main__.py    (exists) — EXTEND: install uvloop, then serve          (11,14)
    config.py      (exists) — EXTEND: provider + cache/http/uvloop/rest settings (04,06,14)
    logging.py     (exists) — EXTEND: RequestIdFilter                      (12)
    telemetry.py   (exists) — EXTEND: tracer/meter accessors              (12)
    schemas.py     (exists) — EXTEND: FetchInput / FetchResponse          (11)
    server.py      (exists) — EXTEND: client+engine+lifespan, REST route  (11)
    tools/
      __init__.py  (exists) — EXTEND: register_fetch_tool
      hello.py     (exists)
      fetch.py     (NEW) — MCP `fetch` tool                               (11)
    fetch/                  NEW engine package — THREE sub-packages; only
      __init__.py           providers/ is file-heavy (shared/ + engine/ stay small)
      shared/               shared tools (leaf — no engine/provider deps)
        __init__.py
        types.py     — FetchResult, FetchRaceResult, ErrorType, ProviderError (01,06)
        html.py      — extract_html_title / extract_markdown_title        (01)
        util.py      — hash_key, validate_api_key, b64 auth, timeouts     (01)
        config.py    — ProviderSecrets (env) + cache/http/uvloop/rest cfg  (04,06)
        http.py      — async HTTP: http_json/http_text/http_raw           (02)
        observability.py — OTEL spans + metrics + request_id              (12)
      engine/               orchestration core
        __init__.py
        failure.py   — is_fetch_failure + detect_grounded_junk           (05)
        cache.py     — FetchCache over py-key-value (MemoryStore default) (06)
        waterfall.py — WATERFALL / BREAKERS / FAILURE constants          (10)
        skip.py      — parse_skip_providers / validate_skip_providers     (10)
        concurrency.py — run_solo / run_parallel / run_sequential        (10)
        orchestrator.py — run_fetch_race (the entry point)               (10)
        runtime.py   — Engine (unified + cache + client) container        (11)
      providers/            ←— THE one chunky package (~33 files)
        __init__.py
        base.py      — FetchProvider ABC                                  (07)
        registry.py  — UnifiedFetchProvider dispatcher + availability     (07)
        _youtube.py  — shared video-id extraction                         (09)
        kimi_proxy.py — scrapfly POST proxy + identity headers            (09)
        tavily.py firecrawl.py jina.py linkup.py spider.py brightdata.py
        scrapedo.py scrapfly.py scrapingbee.py scraperapi.py scrapingant.py
        scrapeless.py scrapegraphai.py olostep.py leadmagic.py you.py
        decodo.py oxylabs.py cloudflare_browser.py                       (07)
        zyte.py diffbot.py opengraph.py scrappey.py                      (08)
        supadata.py serpapi.py sociavault.py kimi.py                     (09)
        github/      — multi-module subpackage                           (09b)
          __init__.py url_parser.py api.py graphql.py handlers.py
          handlers_file.py formatters.py markdown_builder.py
          repo_overview.py constants.py types.py
  tests/             — mirror under tests/fetch/{shared,engine,providers}/ (13)
```

**Reference resolution.** Docs use the short form `fetch/<x>`; resolve it via the
tree above — `shared/` = {types, html, util, config, http, observability};
`engine/` = {failure, cache, waterfall, skip, concurrency, orchestrator, runtime};
`providers/` = {base, registry, the 28 providers, `_youtube`, `kimi_proxy`,
`github/`}. Code sketches throughout the docs use the **fully-qualified** nested
import path (`from omnifetch.fetch.<shared|engine|providers>.X import …`) matching
the tree. The slash-form short references (e.g. `fetch/http` in "Consumes" lines)
are conceptual labels — resolve them via the same tree.

Rationale: only **`providers/`** is file-heavy (28 providers + base + registry +
the github subpackage); `shared/` (6 files) and `engine/` (7) stay small and flat,
so the top of `fetch/` reads as three clear layers rather than a flat dump. Leaf
modules in `shared/` have **no** intra-engine deps → build first/in parallel;
`engine/` depends on `shared/` + `providers/registry`; `providers/` depend on
`shared/`. Every file stays well under the 500-line split threshold.

---

## 0.5 TS → Python primitive mapping (applies everywhere)

| TS / Workers primitive | Python replacement | Notes |
|---|---|---|
| global `fetch()` | `httpx.AsyncClient` (one shared, pooled) | `02-async-http-client.md` |
| `AbortSignal.timeout(ms)` | `httpx.Timeout` + `asyncio.timeout()` | per-provider deadline |
| `AbortSignal.any([...])` | `asyncio` task cancellation | external cancel + timeout |
| `Promise` / `async` | `asyncio` coroutines / `Task` | |
| `Promise.any` / custom race | `asyncio.wait(FIRST_COMPLETED)` loop | `10` — multi-winner race |
| `crypto.randomUUID()` | `uuid.uuid4()` | trace ids |
| `crypto.subtle.digest('SHA-256')` | `hashlib.sha256(...).hexdigest()` | cache keys |
| `crypto.subtle.timingSafeEqual` | `hmac.compare_digest` | REST auth (optional) |
| `btoa(x)` | `base64.b64encode(x.encode()).decode()` | Basic-auth providers |
| `AsyncLocalStorage` | `contextvars.ContextVar` | trace **sink** + `request_id` (#9) only — the HTTP client is injected explicitly (#6), not via contextvar |
| `KVNamespace` (36 h TTL) | `py-key-value` `AsyncKeyValue` — `MemoryStore` default, Redis/Disk drop-in | `06-cache.md` |
| Workers Analytics Engine | OTEL metrics / structured logs | `12` |
| R2 trace JSON blob | OTEL spans (+ optional JSON dump) | `12` |
| `ctx.waitUntil(p)` | `asyncio.create_task` tracked in a background set, or just `await` | no edge eviction in a long-lived process |
| `zod` schema | `pydantic` model / `Annotated` types | `11` |
| `JSON.parse`/`stringify` | `json.loads` / `model_dump_json` | |
| `p-retry` | **`tenacity`** (`AsyncRetrying`, transient-only predicate) | optional bounded transient retry in `http.py`, default **OFF**; retries only `PROVIDER_ERROR`; the waterfall is the primary failover (`02`/`14`) |

**Note on retry**: no fetch provider actually calls `retry_with_backoff` — they
call `http_json`/`http_text` directly and the *orchestrator* provides failover.
So a per-provider retry helper is **optional** (port it for completeness in `01`).
Separately, `http.py` gains an **optional, config-gated, single** transient-error
retry (`02` §02.2 / `14` §14.3) implemented with **`tenacity`** (`AsyncRetrying` +
a `retry_if_exception` predicate), **OFF by default**
(`OMNIFETCH_HTTP_TRANSIENT_RETRIES=0`) — it retries only transient `PROVIDER_ERROR`
(5xx/network) and never subverts the waterfall's failover/fast-fail routing.

---

## 0.6 Authoritative behavior (resolve the config.yaml drift now)

`config.yaml` is a **documentation mirror that has drifted**. The runtime truth is
the `CONFIG` object in `fetch_orchestrator.ts:104-153`. Port **from the code**.

Differences to be aware of (code wins):
- Code waterfall has **`solo: kimi`** as step 3 (`fetch_orchestrator.ts:128`); `config.yaml` omits it.
- Code has a **`github` breaker** (`:106-109`); `config.yaml` omits it.
- `config.yaml:66` lists `http_codes: [403,429,503]` under `failure` — **unused** by the code's `is_fetch_failure` (which keys off content, not status). Drop it.

**Canonical breakers** (`fetch_orchestrator.ts:105-123`, iterate in this order):
1. `github` → provider `github`, domains: `github.com, gist.github.com, raw.githubusercontent.com`
2. `youtube` → provider `supadata`, domains: `youtube.com, youtu.be`
3. `social_media` → provider `sociavault`, domains: `tiktok.com, instagram.com, youtube.com, youtu.be, facebook.com, fb.com, twitter.com, x.com, pinterest.com, reddit.com, threads.net, snapchat.com`

**Canonical waterfall** (`fetch_orchestrator.ts:125-141`):
1. solo `tavily`
2. solo `firecrawl`
3. solo `kimi`
4. parallel `[linkup, cloudflare_browser]`
5. parallel `[diffbot, olostep]`
6. parallel `[scrapfly, scrapedo, decodo]`
7. solo `zyte`
8. solo `brightdata`
9. sequential `[jina, spider, you, scrapeless, scrapingbee, scrapegraphai, scrappey, scrapingant, oxylabs, scraperapi, leadmagic, opengraph]`

**Provider coverage check (28 total, registry `unified/fetch.ts:41-70`):**
- 24 reachable via waterfall (the list above).
- 3 reachable only via breakers: `github`, `supadata`, `sociavault`.
- **1 explicit-only: `serpapi`** — registered & selectable via `provider:serpapi`
  but **never auto-selected** (supadata owns the YouTube breaker). Preserve this.

---

## 0.7 Work-package DAG (build order & parallelism)

Each numbered doc is a self-contained work package with its own acceptance
criteria. Arrows = hard dependency (consumer needs producer's interface).

```
        ┌────────────────────────────────────────────┐
        │ 01 foundations (types, html, util)          │  leaf
        └───────┬───────────────┬──────────────┬──────┘
                │               │              │
        ┌───────▼──────┐  ┌─────▼──────┐  ┌────▼─────────┐
        │ 02 http      │  │ 04 config  │  │ 05 failure   │  (04,05 leaf-ish)
        └───────┬──────┘  └─────┬──────┘  └────┬─────────┘
                │               │              │
        ┌───────▼───────────────▼──────────────▼────────┐
        │ 07 provider base + registry + dispatcher        │
        └───────┬───────────────────────────────┬────────┘
                │                                │
        ┌───────▼────────┐  ┌───────────┐  ┌─────▼──────────┐
        │ 07 generic     │  │ 08 struct │  │ 09 specialized │  (providers fan out)
        │ providers (18) │  │ (4)       │  │ (4) + 09b github│
        └───────┬────────┘  └─────┬─────┘  └─────┬──────────┘
                └──────────┬───────┴──────────────┘
                  ┌────────▼─────────┐   ┌──────────────┐   ┌─────────────┐
                  │ 06 cache         │   │ 12 observ.   │   │ 10 waterfall│
                  └────────┬─────────┘   └──────┬───────┘   └──────┬──────┘
                           └───────────┬────────┴──────────────────┘
                              ┌────────▼─────────────────────┐
                              │ 10 orchestrator + concurrency │  integrator
                              └────────┬──────────────────────┘
                              ┌────────▼─────────┐
                              │ 11 mcp tool+schema│
                              └────────┬──────────┘
                              ┌────────▼─────────┐
                              │ 13 testing+parity │  (also per-package unit tests)
                              └───────────────────┘
   14 concurrency+performance = cross-cutting design constraints read by 02,06,07,10
```

**Recommended sequencing:** 01 → (02, 04, 05 in parallel) → 07 base/registry →
providers (07/08/09 in parallel) → (06, 12) → 10 → 11 → 13. Read **14** before
starting 02 and 10. Doc **15** (Docker/compose packaging) is independent — do it
any time after 11 (it just containerizes the finished server + REST surface).

---

## 0.8 Definition of done (whole project)

1. MCP `fetch` tool present; `tests/fetch/...` + the existing suite pass under
   `uv run pytest` with **100 % coverage**.
2. `uv run pre-commit run --all-files` clean (ruff + mypy --strict + docstrings).
3. Given configured provider keys, `fetch(url)` returns the same
   `{url,title,content,source_provider,...}` shape as the TS tool
   (`tools.ts:322-340`), selecting providers per the §0.6 waterfall.
4. Concurrency parity: a parallel step returns as soon as `target_count` winners
   succeed and **cancels/suppresses** the losing in-flight providers
   (`14`, `10` acceptance tests).
5. No secrets in logs; tool entry/exit logged without payloads.
6. All 28 providers ported and individually unit-tested against mocked HTTP.

Per-package acceptance criteria live in each doc.
