# 13 — Testing, coverage, and TS-parity

> The project gate is **100 % branch coverage** (`pyproject.toml:73 fail_under=100`)
> + `mypy --strict` + ruff. Tests use the in-memory FastMCP `Client`
> (`tests/conftest.py`, `tests/test_hello_tool.py`). This doc defines the test
> layout, the fixtures the engine needs, and a parity harness against the TS server.

---

## 13.1 Test layout (mirror the engine)

```
tests/
  conftest.py                 (EXTEND — env isolation + http client + engine fixtures)
  fetch/
    test_types.py             # 01
    test_html.py              # 01
    test_util.py              # 01  (hash_key cross-impl vector, basic_auth)
    test_http.py              # 02  (5MB cap, status map, redaction, json, per-host cap, bounded retry)
    test_config.py            # 04  (availability combos, 28-entry parity)
    test_failure.py           # 05  (the gate interaction matrix)
    test_cache.py             # 06  (ttl, url-guard, corrupt-miss, backend-agnostic, concurrency)
    test_registry.py          # 07  (28 names, dispatch, active gating)
    providers/
      test_generic.py         # 07  (parametrized over the 19, respx)
      test_structured.py      # 08  (zyte/diffbot/opengraph/scrappey)
      test_specialized.py     # 09  (supadata 202-poll, serpapi, sociavault, kimi)
      test_github_url_parser.py  # 09b (40-URL fixture table)
      test_github_handlers.py    # 09b (golden markdown)
    test_skip.py              # 10  (parser shapes + validation)
    test_concurrency.py       # 10/14 (race, cancellation, timing)
    test_orchestrator.py      # 10  (waterfall, breakers, fast-fail, cache, exhaust)
    test_observability.py     # 12  (no-op default, otel-on, redaction, request-id correlation #9)
    test_invariants.py        # 02 §02.8  (#5 grep: no raw httpx/requests/urllib outside http.py; #6 no global client)
  test_fetch_tool.py          # 11  (in-memory Client end-to-end)
```

---

## 13.2 Fixtures (extend `conftest.py`)

1. **Env isolation** — extend `isolated_env` (`conftest.py:22-28`) to also strip
   **provider** env vars (every name in doc 04) so availability is deterministic.
   Add an autouse fixture (or parametrize) that sets only the keys a test needs.
2. **Mocked HTTP client** — a fixture that creates an `httpx.AsyncClient`
   transported by `respx` (or `httpx.MockTransport`) and **injects it explicitly**
   into `UnifiedFetchProvider(secrets, client)` / `Engine(..., client=client)` (no
   `set_http_client`, #6). Lets provider/orchestrator tests run with **zero
   network**.
3. **Fake `UnifiedFetchProvider`** — a test double whose `fetch_url(url, name)`
   consults a per-test script: `{name: callable(url) -> FetchResult | raises}`,
   with optional `asyncio.sleep`/`asyncio.Event` to control ordering. This is the
   key tool for orchestrator + concurrency tests (no real providers).
4. **Engine fixture** — `Engine(unified=<fake or real-with-respx>,
   cache=FetchCache(MemoryStore()))` (swap in a `fakeredis`-backed `RedisStore` for
   the backend-agnostic test, §06.5 / §13.4).
5. **mcp_server** — extend the existing fixture to build a server whose engine uses
   the fake/mocked provider, so `Client` tests exercise the real tool + schema path
   without network.

---

## 13.3 Concurrency tests (the differentiator — see 14)

Deterministic async tests using `asyncio.Event`/controlled `sleep`:
- **First-winner cancellation**: parallel `[slow, fast]`, `target=1` → returns
  fast; assert the slow provider task was **cancelled** (e.g. it sets a flag in a
  `finally`/`except CancelledError`, and never reaches its "done" line); assert it
  is absent from `providers_failed`.
- **Multi-winner**: `target=2`, three providers, two succeed → both winners
  captured, third cancelled.
- **All-fail-in-parallel**: every provider raises → `[]`, all in `providers_failed`,
  the "all parallel failed" debug log emitted.
- **No orphan tasks**: run under `pytest -W error::RuntimeWarning` and assert no
  "Task was destroyed but it is pending" warnings (validates the `finally`-cancel +
  `gather` settle in doc 10).
- **Timeout → failure**: a provider that exceeds `provider_timeout` raises
  `TimeoutError` recorded as a provider failure, race continues.

These directly verify the §0.8 / doc 10 concurrency acceptance criteria.

---

## 13.4 Provider tests (parametrized, respx)
For each provider, a recorded **success** body (from the upstream's real shape per
the TS interfaces) and a **failure** body. Assert the `FetchResult` mapping
field-by-field. Reuse one parametrized test for the 19 generic providers keyed by
`(name, request matcher, response fixture, expected FetchResult)`. Specialized
providers get bespoke tests (supadata's 202→poll needs a sequenced respx route;
kimi needs the Scrapfly-proxy response shape).

`hash_key` and `basic_auth` get **cross-implementation vectors**: compute the
expected value from the TS algorithm (sha256 hex / `btoa`) and pin it, so the
cache key + auth headers are byte-identical to the TS server (enables a shared
cache or A/B parity later).

---

## 13.5 100 % coverage strategy
- Every `except`/guard branch needs a test (the gate is **branch** coverage).
  Provider empty-body guards, the `is_fetch_failure` matrix, cache corrupt/expired
  paths, orchestrator fast-fail vs fall-through, skip-parser fallbacks.
- Use `# pragma: no cover` **only** for genuinely unreachable defensive lines
  (already excluded: `if TYPE_CHECKING`, `__main__`, `pyproject.toml:74-78`). Keep
  these rare and justified.
- The optional `retry_with_backoff` (doc 01) and REST `/fetch` (doc 11) — if not
  ported, don't ship them (untested code can't exist under a 100 % gate). Port them
  only with tests, or omit.
- **Invariant tests (not coverage-driven)** — `test_invariants.py`: (#5) a static
  grep asserting no `httpx.AsyncClient(`, `import requests`, or `urllib.request`
  appears under `src/omnifetch/fetch/` outside `http.py`; (#6) a DI test asserting
  no provider reads a global/contextvar client — fakes inject a `respx` client via
  `UnifiedFetchProvider(secrets, client)`.

---

## 13.6 TS-parity harness (optional, gated)
A `@pytest.mark.parity` (deselected by default; not counted in coverage) suite
that, given real keys, fetches a fixed URL set through **both** the TS `/fetch`
(or MCP) and the Python tool and diffs:
- `source_provider` selection per the waterfall (allowing provider non-determinism
  on parallel steps — assert it's *one of* the expected step members).
- `is_fetch_failure` verdicts on a corpus of saved bodies (paywalls, challenges,
  short content) — these must match **exactly** (pure function, no network).
- skip-parser outputs on a corpus of LLM-style inputs — exact match.
Run manually / in a nightly job, never in the unit gate.

---

## 13.7 Acceptance criteria
1. `uv run pytest` green with **100 %** branch coverage on `src/omnifetch`.
2. `uv run pre-commit run --all-files` clean (ruff + mypy --strict + docstrings +
   commit-format).
3. The concurrency suite (§13.3) passes under `-W error` (no orphan-task warnings).
4. Every provider has ≥2 tests (success + at least one failure/edge).
5. `is_fetch_failure` + skip-parser parity corpora match the TS outputs exactly.
6. CI matrix (Py 3.11–3.13, `pyproject.toml:18-22`) green; `pip-audit` clean.

## 13.8 Dev dependencies to add
`respx` (httpx mocking), **`fakeredis`** (the backend-agnostic cache test, §06.5),
and, if used, `pytest-httpserver`, `dirty-equals` (loose matching). Pin + regenerate
`uv.lock`. `pytest-asyncio` already present.
