# Critical Comparison: `plan-claude` vs `plan-codex`

> Scope of both plans: port the **URL-fetch** capability of `omnisearch` (TS,
> Cloudflare Workers MCP) into `omnifetch` (Python, FastMCP) — a multi-provider
> waterfall over 28 scraping/extraction APIs with breakers, a 36h cache, and an
> asyncio concurrency model. Search/answer/grounding are out of scope for both.
>
> This document was written after reading **every line** of all 15 `plan-claude`
> files and all 8 `plan-codex` files, and after spot-checking the TS source
> (`fetch_orchestrator.ts`, `grounded_prompts.ts`, `env.ts`, `supadata/index.ts`,
> `tools.ts`) to adjudicate the points where the two plans make different factual
> claims.

---

## 1. High-level overview of differences

### Structure & decomposition

**Claude** is 15 numbered work-package docs organized as a build DAG, plus two
cross-cutting design docs:

- Leaf/foundation: `00-overview.md`, `01-foundations.md` (types/html/util),
  `02-async-http-client.md`, `04-provider-config.md`, `05-failure-detection.md`,
  `06-cache.md`.
- Providers: `07-providers-base-registry-generic.md` (base ABC + registry + 19
  generic), `08-providers-structured.md` (zyte/diffbot/opengraph/scrappey),
  `09-providers-specialized.md` (supadata/serpapi/sociavault/kimi),
  `09b-provider-github.md` (the 11-file GitHub subpackage).
- Integration: `10-orchestrator-concurrency.md`, `11-mcp-tool-server.md`,
  `12-observability.md`, `13-testing-and-parity.md`.
- Cross-cutting: `14-concurrency-and-performance.md` (a dedicated design doc).

Claude proposes a deeply nested engine package (`omnifetch/fetch/{types,html,
util,config,http,failure,cache,waterfall,concurrency,skip,orchestrator,
observability,registry,providers/...}`) and an explicit dependency DAG with a
recommended build order (`00-overview.md` §0.4, §0.7). It also embeds a
TS→Python primitive-mapping table (§0.5) and a "resolve the config.yaml drift"
section that reconstructs the authoritative breaker/waterfall topology from the
TS code (§0.6).

**Codex** is 8 docs organized as a linear, hand-offable pipeline:
`00-index-and-migration-boundaries`, `01-contracts-config-and-module-tree`,
`02-concurrency-waterfall-cache`, `03-http-observability-shared-tools`,
`04-simple-provider-porting`, `05-specialist-provider-porting`,
`06-fastmcp-tool-integration`, `07-test-matrix-acceptance-and-rollout`. Its
target tree puts shared infra at the package top level (`cache.py`, `config.py`,
`errors.py`, `http.py`, `models.py`, `runtime.py`, `tracing.py`) and groups
providers as `providers/simple/` (23), `providers/specialists/` (4) and
`providers/github/` (11). Every doc follows the same rhythm — **Source
References → Files to Add/Modify → Behavior → Acceptance criteria → Required
tests → runnable commands** — and each is explicitly written so "one engineer
can complete it without making architecture decisions outside that file's
scope" (`00-index…` line 30).

### Depth & emphasis

- **Claude leans into design rationale**, especially the two priorities the task
  named first. `14-concurrency-and-performance.md` is an entire doc on the race
  primitive (why `asyncio.wait(FIRST_COMPLETED)` over `gather`/`as_completed`/
  `TaskGroup`), cancellation safety, the lock-free `RaceContext` invariant,
  two-layer timeouts, connection-pool sizing math, HOL blocking, memory/
  backpressure, perf targets, a measurement harness, and an explicit
  "divergences that are net-positive" + "anti-patterns" pair of tables.
  `12-observability.md` is a full OTEL design (a `TraceSink` Protocol seam with a
  no-op default, span tree, metric instruments, redaction). Failure detection
  (`05`) ports the exact TS pattern lists.
- **Codex leans into fidelity discipline and execution mechanics.** `00-index…`
  has a ranked "Runtime source-of-truth priority" and a "Known source-vs-doc
  drift traps" list; `04-simple-provider-porting.md` opens with a
  "Source-Code-Authoritative Provider Ledger" table that enumerates each
  provider's request/auth/body/mapping with the exact `index.ts` line range;
  `07` is a complete test matrix with **named** test functions, a mandatory
  "Current Docs Verification Gate," a handoff checklist, and per-plan runnable
  `conda run -n base uv run …` command blocks. Observability and adversarial
  failure-classification are deliberately held to "second order for v1."

### Coverage & scope

Both cover the same functional surface (28 providers, breakers, waterfall, skip
parsing, cache bypass/two-winner compare, API-native exemptions, the flattened
MCP output). The differences are of *emphasis*, not gross omission:

- Claude is **deeper** on concurrency design, performance design, observability,
  and the failure-gate's exact patterns.
- Codex is **deeper** on per-provider source-fidelity traps, existing-test
  migration, external-docs verification, the GitHub internals (GraphQL fast path
  vs REST fallback, the `constants.ts` truncation caps), and turn-key
  executability.

### Style

Claude is dense and cross-referenced, with many mapping tables and inline code
sketches plus editorial "improvement over TS" callouts. Codex is plainer and
more uniform — bullet lists, ledger tables, and an identical section skeleton
per doc — optimized for scanning and isolated execution over narrative.

---

## 2. Which is better, and why

**Verdict: `plan-claude` is the stronger plan overall, but only narrowly, and
Codex is clearly better on several real dimensions.** The decisive factor is the
task's *stated* emphasis order — (1) concurrency, (2) performance, (3)
hierarchical modules + shared tools — which is exactly where Claude invests the
most and goes deepest, while still matching Codex on functional completeness and
edging it on the single highest-leverage correctness component (the failure
gate). If the weighting prioritized port-risk reduction and turn-key hand-off
instead, Codex would win.

### Dimension-by-dimension

| Dimension | Claude | Codex | Winner |
|---|---:|---:|---|
| Completeness / scope coverage | 9 | 8.5 | Claude (slight) |
| Correctness & fidelity to TS source | 9 | 8.5 | Claude (narrow) |
| Concurrency design | 9.5 | 8 | **Claude** |
| Performance design | 9.5 | 7.5 | **Claude** |
| Module hierarchy / decomposition | 9 | 9 | Tie |
| Testability & acceptance criteria | 9 | 9.5 | Codex (narrow) |
| Clarity | 8.5 | 9 | Codex (slight) |
| Actionability / execute-in-isolation | 8.5 | 9.5 | **Codex** |
| **Weighted toward task emphasis (conc>perf>modules)** | | | **Claude** |

#### Completeness — Claude (slight)
Both cover the functional surface. Claude additionally delivers full designs for
observability (`12`) and performance (`14`) and per-field provider mapping tables
(`07`/`08`/`09`). Codex intentionally minimizes observability (`03` lines
363–381) and the junk detector (`02` lines 165–183), which is the right *scope*
call for the stated priorities but leaves those areas thinner. Edge to Claude on
raw breadth.

#### Correctness & fidelity — Claude (narrow), and genuinely contested
- **Claude's standout fidelity win is the failure gate.** `05-failure-detection.md`
  ports `is_fetch_failure` in the exact TS order (empty-content check *before*
  the `github`/`supadata` API-native bypass — verified at
  `fetch_orchestrator.ts:174-196`) and reproduces `detect_grounded_junk`
  verbatim: the two-tier **TIGHT (~28 always-fire) / AMBIGUOUS (9, gated to
  ≤3000 chars)** pattern split and the `pattern:<p>`/`empty_body` reason strings
  (verified against `grounded_prompts.ts:112-197`). It even explains the
  deliberate overlap between `challenge_patterns` and the junk lists. **Codex
  knowingly approximates this** ("implement a small shared `detect_fetch_junk`…
  Keep this detector short; security and adversarial classification are second
  order for v1," `02` lines 165–183) with a 9-item list that does not match the
  TS lists and drops the 3000-char gating. This is *caller-visible*: the gate
  decides which provider "wins" and what content gets cached for 36h — and the
  TS source itself warns (comment at `fetch_orchestrator.ts:184-194`) that
  getting it wrong **poisons the cross-request cache**. So Claude's verbatim port
  is a meaningful correctness advantage on a uniquely high-leverage component.
- **Codex's offsetting wins** are a more *systematic* anti-drift method that
  catches several per-provider traps Claude misses: serpapi throws a plain
  `Error` (not `ProviderError`) for non-YouTube input and Codex normalizes it to
  `INVALID_INPUT` with a parity test (`00` 147–151, `05` 68–72/307–309);
  `snapchat.com` is in the social breaker but has **no** SociaVault route, so it
  raises `INVALID_INPUT` and falls through (`05` 56–62/271–273); olostep's
  `markdown_hosted_url` is a type-only field with no runtime fallback (`00`
  133–135, `04` 594–595). Claude uses the right fields everywhere but does not
  flag these three.
- Net: each catches things the other misses; Claude's catch (the junk gate) is
  higher-leverage than Codex's three, so a **narrow** Claude edge — but Codex's
  ledger-driven discipline would likely prevent more *total* defects across 28
  providers. Reasonable readers could score this a tie. (Minor nit: Claude's `05`
  header mislabels the challenge list "11 patterns"; the TS `CONFIG.failure`
  actually has 12, all of which Claude *does* list.)

#### Concurrency design — Claude (clear)
This is the task's #1 priority and Claude's strongest area. `14` §14.2 reasons
explicitly about the race primitive, cancellation semantics, and the lock-free
accumulator invariant; `10` §10.3 implements `run_parallel` with an
`asyncio.wait(FIRST_COMPLETED)` loop, `finally`-cancel, and a
`gather(return_exceptions=True)` settle, with a `-W error` orphan-task test
(`10` AC #12, `13` §13.3). Codex's `02` is **behaviorally correct** (cancel
pending after target, suppress late losers, append attempted in configured
order) and well-tested, but it simply picks `asyncio.as_completed` without the
trade-off analysis and has no consolidated concurrency-design rationale.

#### Performance design — Claude (clear)
The task's #2 priority. Claude's `14` gives connection-pool sizing math
(peak-in-flight = 3 per call, ~25–30 concurrent calls before queueing),
keepalive tuning, the deliberate two-layer timeout (httpx per-call *plus* an
outer `provider_timeout` that bounds supadata's non-httpx poll loop — a case a
naive port would miss), uvloop, HOL-blocking analysis, perf targets, and a
fake-provider microbench harness. Codex scatters solid perf *requirements* across
`02` (lines 528–542), `03` (384–390) and `07` (374–402) but has no single
performance design and no pool-sizing math.

#### Module hierarchy / decomposition — Tie
Both nail hierarchy. Claude offers a richer dependency DAG, a leaf-first build
order, explicit <500-line file / ≤45-line function rules, and a named
decomposition of the 250-line `run_fetch_race` into ~10 helpers (`10` §10.4).
Codex offers a cleaner provider taxonomy (`simple/` vs `specialists/`) and a
materially cleaner **DI seam**: a `FetchRuntime` container (config + http_client
+ cache + registry) passed *explicitly* into every `fetch_url(url, runtime)`,
with tests forbidding any provider from importing `load_config()` or
constructing its own client (`01` 290–426). Claude instead injects the shared
HTTP client through a **module-level `contextvar`** (`set_http_client`, `02`),
which is hidden global state with an ordering foot-gun — a genuine architectural
smell relative to Codex's explicit runtime. The two strengths cancel out.

#### Testability & acceptance criteria — Codex (narrow)
Both are strong. Claude's per-doc acceptance criteria are more *behaviorally
precise* (e.g., the `05` failure-gate interaction matrix; the `10` cancellation
assertions; cross-impl `hash_key`/`basic_auth` vectors in `13`). Codex's `07` is
a more *executable QA artifact*: named test functions for every module, an
explicit plan to migrate the existing `test_schema_enforcement.py`/
`test_hello_tool.py` to select tools **by name not index**, relative-timing
(non-flaky) concurrency/perf tests, a live-smoke marker, and a handoff checklist.
Edge to Codex for hand-off readiness.

#### Clarity — Codex (slight)
Codex's uniform skeleton and ledger tables are easier to scan; Claude is denser
and more editorial (though its tables are excellent). Slight Codex.

#### Actionability — Codex (clear)
Codex is purpose-built for isolated execution: implementation order, per-plan
runnable command blocks, a source-of-truth priority list, the provider ledger as
a one-glance map, a docs-verification gate, and a handoff checklist. Claude's
self-contained work packages with "interfaces exposed/consumed" are good, but a
few lean on "read the TS file in full / infer" (notably the GitHub handler
bodies, `09b`) rather than giving the same turn-key detail.

### Why Claude wins on balance
The task explicitly ranks concurrency and performance first and second; Claude is
decisively deeper on both, ties on module hierarchy, leads on completeness, and
holds a narrow fidelity edge on the highest-stakes component (the cache-poisoning
failure gate). Codex's advantages are real and concentrated in execution
discipline (testability, actionability, anti-drift fidelity breadth, existing-test
migration, docs verification) — enough that under a "minimize port risk / maximize
hand-off" weighting Codex would win, but not enough to overturn Claude under the
stated emphasis. It is a close call, not a blowout.

---

## 3. Improvements Codex's plan has that Claude's does not

Each item cites where it lives in the Codex plan and the corresponding gap in
Claude's.

1. **A consolidated source-of-truth priority + per-provider drift ledger.**
   Codex `00-index…` "Deep Source Pass Findings" (lines 88–157) ranks authority
   (orchestrator `CONFIG` > `unified/fetch.ts` PROVIDERS tuple > each
   `index.ts` > README/`config.yaml`/`AGENTS.md`) and lists named drift traps;
   `04-simple-provider-porting.md` "Source-Code-Authoritative Provider Ledger"
   (lines 82–111) is a single table mapping each provider to request/auth/body/
   content-mapping with exact TS line ranges. **Claude** has the equivalent facts
   but scatters them across `00` §0.6, `04`, `07`, `08`, `09` with no single
   ranked-authority statement or one-glance ledger.

2. **serpapi plain-`Error` normalization.** Codex `00` 147–151 and `05` 68–72/
   307–309 flag that TS serpapi throws a bare `Error` (not `ProviderError`) for
   non-YouTube input *before* its try/catch, and deliberately normalizes it to
   `ProviderError(INVALID_INPUT)` with a parity test documenting the intentional
   divergence. **Claude** `09` §09.2 treats serpapi as explicit-only but never
   addresses the untyped-error problem, so an explicit `provider:serpapi` call on
   a non-YT URL would surface an unattributed error lacking `INVALID_INPUT`
   semantics.

3. **snapchat breaker / missing-route fall-through.** Codex `05` 56–62 and
   271–273 (and `07` 251) call out that `snapchat.com` is in the `social_media`
   breaker domains but SociaVault has **no** snapchat route, so it raises
   `INVALID_INPUT` and falls through — with an explicit test. **Claude** `09`
   §09.3 lists the 9 routes and the generic no-match fall-through but never names
   the snapchat breaker-vs-route mismatch.

4. **olostep "no hosted-URL fallback" trap.** Codex `00` 133–135 and `04`
   594–595 warn that olostep's `markdown_hosted_url` is type-only and the source
   reads `result.markdown_content` exclusively — do not invent a fallback.
   **Claude** `07` §07.3 maps the correct field but gives no such warning.

5. **"All HTTP goes through the shared client" as an enforced, tested
   invariant.** Codex `03` "Provider HTTP Usage Rules" (326–361) bans
   `httpx.AsyncClient()`, `requests`, and `urllib` outside `http.py` and mandates
   a static-grep/review test; it specifically routes the two TS bare-`fetch`
   bypass sites — supadata's 202 path and GitHub raw-file/wiki fetches — through
   the shared client (`expected_statuses=(202,)`), citing
   `supadata/index.ts:97` and `handlers-file.ts:153/195`. **Claude** does route
   through its shared client too (it proposes `_request`/`http_raw` for
   supadata's status, `09` §09.1), but never elevates "no raw client anywhere" to
   a tested rule, so the invariant is assumed rather than guarded.

6. **Explicit runtime DI instead of a contextvar-injected HTTP client.** Codex
   `01` 290–426 defines a `FetchRuntime` container passed explicitly into every
   `fetch_url(url, runtime)`, with acceptance tests that no provider imports
   `load_config()` or constructs its own client. **Claude** `02` injects the
   shared client via a module-level `contextvar` + `set_http_client`, which is
   hidden global state with a "must-set-before-first-call" foot-gun and a subtler
   test setup. (Claude's contextvar use for the *trace sink* in `12` is fine —
   only the HTTP-client contextvar is the weaker choice.)

7. **Existing-test migration discipline.** Codex `06` 312–334 and `07` 60–79/
   268–290 explicitly update `tests/test_schema_enforcement.py` and
   `tests/test_hello_tool.py` to select tools **by name, not list index** (they
   currently assume index 0 is `say_hello`), update `tests/test_main.py` for
   runtime construction, keep `say_hello`, and keep the `_REGISTRARS`-length
   test. **Claude** `11` §11.3 mentions the `_REGISTRARS`-length and hello-logging
   tests but never addresses the index-based tool selection that breaks the
   moment a second tool is registered.

8. **A mandatory current-docs verification gate.** Codex `07` 338–371 requires
   checking the live FastMCP / httpx / cachetools / pydantic-settings / pytest-
   mock docs and recording URLs + date before coding against those APIs.
   **Claude** flags only the FastMCP 3.4.2 lifespan API as a one-off check (`11`
   §11.4); it has no systematic external-API verification step.

9. **Request-id/trace-id propagation into log records.** Codex `03` 249–296
   defines `tracing.py` with `request_id_var`/`trace_id_var`, a `request_context`
   manager, and a logging filter that injects the id into every record —
   mirroring the TS `run_with_request_id(crypto.randomUUID())` at
   `tools.ts:307`, and tested for cross-task isolation. **Claude**'s `12` has a
   contextvar trace *sink* and OTEL spans but does not give per-request
   `request_id` first-class presence in the structured logs, which is the cheaper
   day-to-day correlation handle under concurrent calls.

10. **GitHub internals specified to a deeper, source-cited level.** Codex `05`
    376–615 gives the GraphQL-fast-path-then-REST-fallback strategy
    (`repo-overview.ts:31-43`), the targeted depth-2 tree-child fetch capped by
    `MAX_TREE_CHILDREN_DIRS` (`graphql.ts:30-91`), the `Promise.all`→
    `asyncio.gather` parallel batches (`repo-overview.ts:278-293`), the
    ambiguous blob/tree split (`handlers-file.ts:19-43`), the root-README-
    upgrades-to-overview behavior (`:138-176`), and the full set of `constants.ts`
    truncation/pagination/noisy-dir caps to preserve. **Claude** `09b` nails the
    URL parser and the dispatch table but explicitly marks the handler bodies
    "infer / read in full" and does not enumerate the GraphQL/REST repo-overview
    strategy or the truncation caps at the same level.

11. **Turn-key execution scaffolding.** Codex provides an "Implementation Order"
    (`00` 296–307), per-plan runnable `conda run -n base uv run …` command blocks,
    and a final "Handoff Checklist" (`07` 487–509). **Claude** has the build-order
    DAG (`00` §0.7) and per-doc acceptance criteria but no literal runnable
    command blocks or consolidated handoff checklist.

12. **`"all"` is not a magic skip token.** Codex `02` 254–256 (and its test list)
    explicitly preserves that a literal `"all"` parses to one unknown provider
    name and is rejected by validation — not treated as "skip everything."
    **Claude** `10` §10.2 describes the parser thoroughly but never calls out this
    specific non-magic behavior.

---

## 4. Changes required to the Claude plan to fix #3

A checklist an engineer can execute directly against the `plan-claude/` files.
Each item maps to the correspondingly numbered gap in §3.

- [ ] **(3.1) Add a source-of-truth ledger.** In `plan-claude/00-overview.md`,
  add a new section **§0.9 "Source-of-truth priority + per-provider drift
  ledger."** State the ranked authority (orchestrator `CONFIG` → `unified/
  fetch.ts` PROVIDERS → each provider `index.ts` → README/`config.yaml`/
  `AGENTS.md` as advisory only) and fold the per-provider gotchas currently
  scattered in `04` §04.1 notes, `07` §07.3, `08`, and `09` into one table keyed
  by provider with the exact TS `index.ts` line range and the "commonly
  mis-documented" column (mirroring Codex `04` lines 82–111).

- [ ] **(3.2) serpapi error normalization.** In
  `plan-claude/09-providers-specialized.md` §09.2, add a note that TS serpapi
  throws a plain `Error` for non-YouTube input *before* its try/catch, and that
  the Python port must raise `ProviderError(ErrorType.INVALID_INPUT, …)` so an
  explicit `provider:serpapi` call on a non-YT URL is typed. Add an acceptance
  criterion + a parity test documenting the intentional divergence from TS.

- [ ] **(3.3) snapchat breaker/route mismatch.** In `09-providers-specialized.md`
  §09.3, add: `snapchat.com` matches the `social_media` breaker but has no
  SociaVault route → `detect_route` returns no match → `ProviderError(
  INVALID_INPUT)` → orchestrator falls through. Add an acceptance test, and
  cross-reference it from `10-orchestrator-concurrency.md` §10.6.

- [ ] **(3.4) olostep no-fallback warning.** In
  `plan-claude/07-providers-base-registry-generic.md` §07.3 (olostep row /
  "Per-provider notes"), add: do **not** implement a `markdown_hosted_url`
  fallback; the source reads `result.markdown_content` only.

- [ ] **(3.5) Enforce the shared-client invariant.** In
  `plan-claude/02-async-http-client.md`, add a section **"§02.8 No raw HTTP
  client rule"**: providers may only use `http_json`/`http_text`; ban
  `httpx.AsyncClient()`, `requests`, `urllib` outside `http.py`/tests. Add a
  static-grep test to `plan-claude/13-testing-and-parity.md` §13.5. In
  `09-providers-specialized.md` §09.1 and `09b-provider-github.md` §09b.4, state
  that supadata's initial 202 call and GitHub raw-file/wiki fetches go through
  the shared client (supadata via a status-returning helper; note that an
  `expected_statuses=(202,)` style requires exposing the status code, so keep the
  `_request`-returns-`(raw, status)` design).

- [ ] **(3.6) Replace the contextvar HTTP client with explicit DI.** This touches
  three docs. In `02-async-http-client.md`, drop the module-level
  `set_http_client`/`_client` contextvar; have `http_json`/`http_text` take the
  client (or a runtime/engine) as a parameter. In
  `07-providers-base-registry-generic.md` §07.1/§07.2, give the `FetchProvider`
  base (or `UnifiedFetchProvider`) the client at construction and pass it down to
  `fetch_url`. In `11-mcp-tool-server.md` §11.4, make the `Engine`/runtime carry
  the `httpx.AsyncClient` and thread it explicitly. Add an acceptance criterion:
  no provider reads a global/contextvar client; fakes inject a client directly.
  (Keep the contextvar **only** for the trace sink in `12`.)

- [ ] **(3.7) Existing-test migration.** In `11-mcp-tool-server.md` §11.3 (or a
  new subsection of `13`), add explicit steps to update
  `tests/test_schema_enforcement.py` and `tests/test_hello_tool.py` to select
  tools **by name, not index**; update `tests/test_main.py` for runtime/engine
  construction; keep `say_hello`; keep the `len(tools) == len(_REGISTRARS)` test.
  Add these as acceptance criteria.

- [ ] **(3.8) Add a current-docs verification gate.** In
  `13-testing-and-parity.md`, add a subsection **"§13.9 Current-docs verification
  gate"** requiring the implementer to check the live FastMCP, httpx (async
  client + streaming + timeouts + limits), pydantic-settings, and the chosen
  HTTP-mock library docs, recording URLs + date before coding against those APIs.
  Generalize the existing one-off FastMCP-lifespan check in `11` §11.4 to point
  at this gate.

- [ ] **(3.9) First-class request-id in logs.** In `12-observability.md`, add a
  `request_id`/`trace_id` contextvar plus a logging filter that injects
  `request_id` into every fetch log record, generated per call (mirror TS
  `run_with_request_id(crypto.randomUUID())`). Set it at the tool entry
  (`11-mcp-tool-server.md` §11.2) or at the top of `run_fetch_race`
  (`10` §10.4). Acceptance: two concurrent in-memory tool calls show distinct
  `request_id`s with no cross-contamination.

- [ ] **(3.10) Deepen the GitHub spec.** In `09b-provider-github.md`, add a
  section **"§09b.7 repo-overview strategy + truncation caps"**: GraphQL fast
  path first with REST fallback on any GraphQL error (`repo-overview.ts:31-43`);
  root-tree-then-targeted-depth-2-children capped by `MAX_TREE_CHILDREN_DIRS`
  (`graphql.ts:30-91`); the two `asyncio.gather` parallel batches
  (`repo-overview.ts:278-293`); ambiguous blob/tree split
  (`handlers-file.ts:19-43`); root-README-upgrades-to-overview (`:138-176`); and
  enumerate the `constants.ts` truncation/pagination/noisy-dir caps to preserve
  (`constants.ts:3-11/15-61/67-102/106-118/118-129`).

- [ ] **(3.11) Add execution scaffolding.** In `00-overview.md` add an
  "Implementation Order" list and, in each work-package doc's acceptance section,
  a literal runnable command block (e.g. `uv run pytest tests/fetch/... && uv run
  mypy && uv run ruff check`). Add a consolidated **"Handoff checklist"** to
  `13-testing-and-parity.md` §13.7 mirroring Codex `07` 487–509.

- [ ] **(3.12) `"all"` is not magic.** In `10-orchestrator-concurrency.md` §10.2,
  add a note that a literal `"all"` parses to a single unknown name and is
  rejected by `validate_skip_providers` (not "skip everything"); add it to the
  §10.6 skip-parsing acceptance test.

- [ ] **(bonus correctness nit)** In `05-failure-detection.md`, fix the
  `CONFIG.failure.challenge_patterns` header that says "11 patterns" — the TS
  list (`fetch_orchestrator.ts:145-151`) has **12** (all already listed in
  Claude's `_CHALLENGE_PATTERNS`).
