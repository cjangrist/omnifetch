# 11 — MCP `fetch` tool, schemas, and server wiring

> Surfaces the engine as a FastMCP tool. Source: `server/tools.ts:254-350`
> (`register_fetch_tool` + I/O schema + the tool description), with DI + lifespan
> adapted to a long-lived Python process. Plus a lightweight, **toggleable** REST
> `POST /fetch` (#5, `server/rest_fetch.ts`) — required, second-order to the tool.

---

## 11.1 `schemas.py` additions (extend the existing file)

Mirror the MCP `inputSchema`/`outputSchema` (`tools.ts:274-305`).

```python
# Input — url + optional skip_providers (string OR list).
FetchUrl = Annotated[str, Field(description="The URL to fetch — any public URL.",
                                min_length=1, max_length=2000)]
SkipProviders = Annotated[
    str | list[str] | None,
    Field(default=None, description=(
        "Provider names to skip in the waterfall. Comma-separated string, a "
        "JSON-encoded array string, or a native array. Triggers a 2-provider "
        "compare (alternative_results), bypasses cache, ~2x cost+latency.")),
]

class FetchProviderFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    error: str
    duration_ms: float

class FetchAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str
    content: str
    source_provider: str
    metadata: dict[str, Any] | None = None

class FetchResponse(BaseModel):
    """Structured result of the fetch tool (mirrors tools.ts:282-305)."""
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str
    content: str
    source_provider: str
    total_duration_ms: float
    metadata: dict[str, Any] | None = None
    providers_attempted: list[str] | None = None
    providers_failed: list[FetchProviderFailure] | None = None
    alternative_results: list[FetchAlternative] | None = None
```
- `url` uses Pydantic `str` + length (TS used `z.string().url()`; a strict URL
  validator is optional — the orchestrator/providers tolerate any string and
  `mask_error_details` hides parse errors. Add `AnyUrl` only if you want a 422 at
  the boundary; note it rejects some valid-but-unusual URLs).
- The response is a **flattened** view of `FetchRaceResult` (primary fields
  hoisted, `alternative_results` mapped) — exactly `tools.ts:322-340`.

---

## 11.2 `tools/fetch.py` — the tool (mirror `register_fetch_tool`)

```python
"""The `fetch` MCP tool: multi-provider URL→markdown waterfall.

Thin adapter over fetch.orchestrator.run_fetch_race — parses skip_providers,
runs the race against the injected engine, and flattens FetchRaceResult into the
schema-enforced FetchResponse.
"""
from __future__ import annotations
import logging
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from logdecorator.asyncio import async_log_on_end, async_log_on_start
from mcp.types import ToolAnnotations
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.engine.orchestrator import run_fetch_race
from omnifetch.fetch.engine.skip import parse_skip_providers, validate_skip_providers
from omnifetch.fetch.shared.types import ProviderError
from omnifetch.logging import get_logger
from omnifetch.schemas import FetchResponse, FetchUrl, SkipProviders

_LOGGER = get_logger("tools.fetch")
_TOOL_DESCRIPTION = "..."   # port tools.ts:258-266 verbatim (the long blurb)
_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=True,
    destructiveHint=False, title="URL Fetch (multi-provider waterfall)")


def register_fetch_tool(server: FastMCP, engine: Engine) -> None:
    @async_log_on_start(logging.INFO, "Tool call: fetch(url={url!r})", logger=_LOGGER)
    @async_log_on_end(logging.INFO, "Tool exit: fetch", logger=_LOGGER)
    async def fetch(url: FetchUrl, skip_providers: SkipProviders = None,
                    ctx: Context | None = None) -> FetchResponse:
        skip = parse_skip_providers(skip_providers)
        if skip:
            _, unknown = validate_skip_providers(skip, engine.unified.active_names)
            if unknown:
                raise ToolError(
                    f"Unknown skip_providers names: {', '.join(unknown)}. "
                    f"Valid: {', '.join(engine.unified.active_names)}")
        try:
            race = await run_fetch_race(engine.unified, url, cache=engine.cache,
                                        skip_providers=skip)
        except ProviderError as exc:
            raise ToolError(str(exc)) from exc      # surface provider message
        return _to_response(race, skip)

    server.tool(name="fetch", title="URL Fetch (multi-provider waterfall)",
                description=_TOOL_DESCRIPTION, annotations=_TOOL_ANNOTATIONS)(fetch)
```
`_to_response(race, skip)` flattens `FetchRaceResult` → `FetchResponse`
(`tools.ts:322-340`): hoist `result.{url,title,content}`, `provider_used →
source_provider`, `total_duration_ms`, `metadata`, `providers_attempted`,
`providers_failed`, and map `alternative_results`.

**Error surfacing**: `mask_error_details=True` (`server.py:37`) masks *unexpected*
exceptions, but `ToolError` messages are passed through to the client. Wrap
expected `ProviderError`s in `ToolError(str(exc))` so the LLM sees "All providers
failed…/Unknown skip_providers…" (parity with the TS `format_error` path,
`tools.ts:345-347`). Let truly unexpected exceptions mask.

**Logging parity**: `@async_log_on_start/_on_end` log entry/exit + the `url` arg
but **not** the content (the hello test pattern,
`tests/test_hello_tool.py:72-81`) — RULE_09 #1. The per-fetch `request_id` (#9) is
bound inside `run_fetch_race` (doc 12 §12.6), so every engine/provider/HTTP log line
for one tool call shares an id and the structured format includes `%(request_id)s`.

---

## 11.3 `tools/__init__.py` — thread the engine to registrars

The hello registrar takes only `server`; the fetch registrar needs the `engine`.
Refactor `_REGISTRARS` to receive a shared deps object (explicit DI, RULE_09 #8).

```python
from omnifetch.fetch.engine.runtime import Engine

_REGISTRARS = (
    lambda server, engine: register_hello_tool(server),   # ignores engine
    register_fetch_tool,                                   # (server, engine)
)

def register_tools(server: FastMCP, engine: Engine) -> None:
    for register in _REGISTRARS:
        register(server, engine)
```
(Or give `register_hello_tool` an optional `engine` param. Keep the
`_REGISTRARS`-length test in `test_hello_tool.py:46-49` working — it asserts
`len(tools) == len(_REGISTRARS)`.)

**Existing-test migration (#7) — required, or the scaffold suite breaks.** Adding a
second tool invalidates the scaffold's **index-based** assumptions; update these
**with** this change (not after):
- `tests/test_hello_tool.py:52-58` (`test_tool_metadata_is_advertised`) takes
  `(await client.list_tools())[0]` and asserts it is "Say Hello" — **select by name**
  instead: `next(t for t in tools if t.name == "say_hello")`. Tool order is **not**
  guaranteed once two tools are registered.
- `test_every_registrar_produces_a_tool` (`:46-49`, `len(tools)==len(_REGISTRARS)`)
  stays valid (now `2 == 2`) — keep it.
- `tests/test_schema_enforcement.py` — if it indexes `tools[0]`, switch to by-name.
- `tests/test_main.py` — update for the new `build_server` (engine + client
  construction) if it asserts the old signature.
Keep `say_hello` (don't delete the demo tool); just make every assertion
**order-independent**.

---

## 11.4 `fetch/engine/runtime.py` + `server.py` lifespan (httpx client lifecycle)

The shared `httpx.AsyncClient` must be created on startup and closed on shutdown.
Create the client in `build_server`, **inject it explicitly** into the registry
(#6, doc 02), and use FastMCP's async `lifespan` solely to `aclose()` it on
shutdown — no module-level setter.

```python
# fetch/engine/runtime.py
@dataclass(frozen=True, slots=True)
class Engine:
    unified: UnifiedFetchProvider     # holds the injected client (#6)
    cache: FetchCache
    client: httpx.AsyncClient         # owned here; aclose()d by the lifespan
```

```python
# server.py (extend build_server)
import contextlib, httpx
from omnifetch.config import load_config
from omnifetch.fetch.engine.cache import FetchCache, build_cache_store
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.providers.registry import UnifiedFetchProvider

def build_server() -> FastMCP:
    config = load_config()
    # ONE shared client, constructed here and INJECTED explicitly into the registry
    # (#6 — no module-level/contextvar client); the lifespan only closes it.
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=40)
    client = httpx.AsyncClient(http2=True, follow_redirects=True, limits=limits)
    engine = Engine(unified=UnifiedFetchProvider(config.providers, client),
                    cache=FetchCache(build_cache_store(config.server)),
                    client=client)

    @contextlib.asynccontextmanager
    async def lifespan(_server: FastMCP):
        try:
            yield                          # client built above; used on the loop
        finally:
            await engine.client.aclose()   # release pooled connections on shutdown

    server = FastMCP(name=_NAME, version=_VERSION, instructions=_INSTRUCTIONS,
                     strict_input_validation=True, mask_error_details=True,
                     lifespan=lifespan)
    register_tools(server, engine)
    return server
```
- **Verify the exact FastMCP 3.4.2 lifespan API** (`lifespan=` kwarg vs a
  decorator vs `@asynccontextmanager` returning state) — adjust to the installed
  version. This is the one integration point that needs a docs/API check (RULE_04).
- **Explicit client injection (#6)**: the client is created in `build_server` and
  passed into `UnifiedFetchProvider(config.providers, client)` (and carried on the
  `Engine`); the lifespan only `aclose()`s it on shutdown. There is **no**
  `set_http_client` and **no** contextvar client. For unit tests, construct
  `UnifiedFetchProvider(secrets, respx_client)` / `Engine(..., client=respx_client)`
  with a `respx`-mocked client directly — nothing global to set (doc 13).
- **Client lifecycle note**: `httpx.AsyncClient` is constructed outside a running
  loop (fine — it binds lazily) and closed inside the lifespan; the in-memory test
  `Client` runs the lifespan in the same task tree, so the close is exercised.
- `config = load_config()` reads provider secrets once (frozen). `__main__.main()`
  already calls `load_dotenv()` before `build_server` indirectly — keep that order,
  and add `install_uvloop(config.server)` (doc 14 §14.1) **before** `run_server()` so
  the chosen event loop is set process-wide before FastMCP creates it (#1).

---

## 11.5 Lightweight REST `POST /fetch` — required, second-order, toggleable (#5)
A thin REST surface over the **same** engine — **implemented, not optional** — for
non-MCP clients (curl, Open WebUI, a browser, the compose smoke check, doc 15). It
is deliberately **second-order**: a convenience that mirrors the MCP tool and can be
turned off with one env var. Mounted as a FastMCP custom route, active only on the
HTTP/SSE transports:

```python
# server.py — mounted when REST is enabled (no-op on stdio)
if config.server.rest_fetch:
    @server.custom_route("/fetch", methods=["POST"])
    async def rest_fetch(request: Request) -> JSONResponse:
        body = await request.json()
        url = (body or {}).get("url")
        if not isinstance(url, str) or not url.strip():
            return JSONResponse({"error": "url is required"}, status_code=400)
        skip = parse_skip_providers(body.get("skip_providers"))
        try:
            race = await run_fetch_race(engine.unified, url, cache=engine.cache,
                                        skip_providers=skip)
        except ProviderError as exc:
            return JSONResponse({"error": str(exc)}, status_code=_status_for(exc))
        return JSONResponse(_to_response(race, skip).model_dump())
```

Behavior mirrors `rest_fetch.ts`: body `{url, provider?, skip_cache?,
skip_providers?}`; validate (url required, ≤2000, valid URL; reject
`provider`+`skip_providers` combo, `:88-93`); call `run_fetch_race`; map errors to
status (`RATE_LIMIT→429, NOT_FOUND→404, INVALID_INPUT→400, else 502`, `:153-156`).
Reuses `_to_response` from §11.2 — **zero engine duplication**, the REST path is a
~25-line adapter.

- **Toggle**: `rest_fetch: bool = True` on `ServerSettings` (`OMNIFETCH_REST_FETCH`,
  default **on**). Set it `false` to drop the route entirely — one env var, no
  rebuild. On stdio transport the route is simply never reached.
- **Security is second-order** (project-wide): unauthenticated by default; gate it at
  your ingress, or add the optional `hmac.compare_digest` bearer check (`01` §01.3)
  later. Do **not** block the feature on auth — it's a convenience surface.
- **Cloud-agnostic tie-in**: this is exactly the surface docker-compose exposes (doc
  15) and the simplest smoke test — `curl -s localhost:8000/fetch -d '{"url":"…"}'`.

---

## 11.6 Acceptance criteria
1. `fetch` tool registered; `client.list_tools()` includes `"fetch"` with the
   right title + `readOnlyHint`/`idempotentHint`/`openWorldHint=True`.
2. In-memory `Client.call_tool("fetch", {"url": ...})` returns a `FetchResponse`
   with `source_provider` set and `content` non-empty (against mocked providers).
3. `skip_providers="bogus"` → `ToolError` "Unknown skip_providers names: bogus…".
4. A `skip_providers="tavily"` call → cache bypass + (when ≥2 providers succeed)
   `alternative_results` populated; ~2 providers attempted.
5. Engine error (all providers fail) → `ToolError("All providers failed…")`,
   `is_error=True` at the client.
6. Tool entry/exit logged with the URL but **not** the fetched content
   (assert via `caplog`, parity with `test_hello_tool.py:72-81`).
7. `len(tools) == len(_REGISTRARS)` still holds (hello + fetch = 2).
8. **Client lifecycle + DI (#6)**: the shared client is injected into the registry
   at construction and `aclose()`d on lifespan exit (assert it's usable inside a
   tool call and closed after); assert there is **no** module-level client setter.
9. **REST `/fetch` (#5)**: with `--transport http` + `OMNIFETCH_REST_FETCH=true`,
   `POST /fetch {"url": ...}` returns the same flattened `FetchResponse` JSON as the
   MCP tool (reusing `_to_response`); `OMNIFETCH_REST_FETCH=false` → route absent
   (404); error mapping matches `rest_fetch.ts` (429/404/400/502).
10. **Existing tests migrated (#7)**: `test_hello_tool.py` selects tools by **name**
    (not index `[0]`); the full scaffold suite stays green after `fetch` is registered.
11. `mypy --strict` + ruff clean; tool fn ≤45 lines (push mapping into `_to_response`).

## 11.7 Interfaces
**Exposes:** `register_fetch_tool`, the toggleable `/fetch` REST route (#5),
`FetchResponse`/`FetchInput` schemas, `Engine`, extended `build_server`/
`register_tools`. **Consumes:** `fetch/engine` (orchestrator, runtime, cache, skip),
`fetch/providers` (registry), `fetch/shared` (http, types), `fastmcp`.
