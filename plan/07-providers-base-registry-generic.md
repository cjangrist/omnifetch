# 07 — Provider base, registry/dispatcher, and the 19 generic providers

> Source: `providers/unified/fetch.ts` (107, registry+dispatcher),
> `providers/index.ts` (110, init/availability), `server/tools.ts:35-39,51-105`
> (the singleton registry), and the 19 uniform `providers/fetch/<n>/index.ts`.

---

## 07.1 `fetch/providers/base.py` — the `FetchProvider` ABC

Source contract: `common/types.ts:35-39` (`FetchProvider`). Every provider is
**fully self-describing** and **self-registering**: it declares its `name`,
endpoint, timeout, and required secret(s) as class attributes, and
`__init_subclass__` adds each concrete subclass to a module registry **on import**.
Adding a provider is then a **one-file** operation (overview §0.4) — no central
registry/config dict to edit. This deliberately sheds the TS `PROVIDERS`-array
"add one line here" workaround, which existed only because Cloudflare Workers bundle
statically (no runtime import/registration); Python self-registers.

```python
"""Abstract base + self-registering registry for all fetch providers.

A provider owns one upstream API. It DECLARES name/base_url/timeout_ms/
required_secrets as class attrs and implements fetch_url; __init_subclass__ registers
every concrete subclass into _REGISTRY at import time. fetch_url raises ProviderError
on failure; the orchestrator decides failover from the error type.
"""
from __future__ import annotations
import abc
import inspect
from typing import ClassVar
import httpx
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import FetchResult

# name -> concrete provider class, filled by __init_subclass__ on import.
_REGISTRY: dict[str, type[FetchProvider]] = {}


class FetchProvider(abc.ABC):
    # ── per-provider declarations (override in each subclass) ──
    name: ClassVar[str]
    description: ClassVar[str] = ""
    base_url: ClassVar[str] = ""
    timeout_ms: ClassVar[int] = 30_000
    required_secrets: ClassVar[tuple[str, ...]] = ()   # ProviderSecrets attr names

    def __init_subclass__(cls, **kw: object) -> None:
        super().__init_subclass__(**kw)
        if inspect.isabstract(cls):           # skip any intermediate abstract base
            return
        if not getattr(cls, "name", ""):
            raise TypeError(f"{cls.__name__} must set a non-empty `name`")
        if cls.name in _REGISTRY:
            raise TypeError(f"duplicate provider name {cls.name!r} "
                            f"({cls.__name__} vs {_REGISTRY[cls.name].__name__})")
        _REGISTRY[cls.name] = cls

    def __init__(self, secrets: ProviderSecrets, client: httpx.AsyncClient) -> None:
        self._secrets = secrets
        self._client = client          # injected shared client (#6) — never global

    @property
    def timeout_s(self) -> float:
        return self.timeout_ms / 1000

    @classmethod
    def is_available(cls, secrets: ProviderSecrets) -> bool:
        """True iff every declared required secret is set. oxylabs (2 secrets) and
        cloudflare_browser (3) just list all of theirs in `required_secrets` — no
        special-casing. Override only for genuinely exotic rules."""
        return all(getattr(secrets, attr, None) for attr in cls.required_secrets)

    @abc.abstractmethod
    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch `url` and return a FetchResult, or raise ProviderError."""
```

> **"Magical" add-a-provider** (your goal): create `providers/<name>.py` with a
> `FetchProvider` subclass declaring its attrs + `fetch_url`. The package
> auto-imports it (registry below), so it self-registers — no edit to a central
> registry or config dict. The only unavoidable extra touch-points are its env-var
> field on `ProviderSecrets` (the typed env contract, doc 04) and, if it should be
> auto-selected, a slot in the waterfall (doc 10 — names are hardcoded there by
> design; `validate_registry()` guards that contract).

**Boilerplate-reduction rule (not a leaky template):** each provider's `fetch_url`
follows the same 4 beats — (1) `key = validate_api_key(...)`, (2) `data = await
http_json/http_text(...)`, (3) guard + map to `FetchResult`, (4) wrap exceptions
via `handle_provider_error(exc, self.name, "fetch URL content")`. Keep these
explicit per provider (≈25–40 lines each, within RULE_09 #9) rather than a magic
base method — the request/response shapes differ too much to abstract cleanly. Beat
(2) always passes the injected client first: `await http_json(self._client, …)` (#6).

> Mechanical caveat: in TS, `handle_provider_error` is called in the `catch` and
> `fetch_url` has no return after it (TS sees `never`). In Python, wrap the body in
> `try/except Exception as exc: handle_provider_error(...)` — and because mypy
> can't prove `handle_provider_error` is `NoReturn` unless annotated, **annotate it
> `-> NoReturn`** (doc 01) so no spurious "missing return" is reported.

---

## 07.2 `fetch/providers/registry.py` — registry + dispatcher + availability

Auto-imports the providers package (so subclasses self-register, §07.1), exposes the
dispatcher, and **guards** the registry against the waterfall name-contract.

```python
"""Auto-import + unified dispatcher + registry guard.

import_all_providers() imports every module under providers/ so each provider's
__init_subclass__ runs and fills FetchProvider._REGISTRY — that IS registration.
UnifiedFetchProvider builds instances for the available providers and dispatches
fetch_url(url, name). validate_registry() fails fast on drift vs. the waterfall.
"""
from __future__ import annotations
import importlib
import pkgutil
import httpx
from omnifetch.fetch.providers import base
from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.logging import get_logger

_LOGGER = get_logger("fetch.registry")
_INFRA_MODULES = {"base", "registry"}      # not providers


def import_all_providers() -> dict[str, type[FetchProvider]]:
    """Import every provider module so subclasses self-register. Idempotent
    (importlib caches); call once at startup. Returns the live registry. The
    `github` subpackage registers via its own __init__ importing GitHubFetchProvider."""
    import omnifetch.fetch.providers as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name not in _INFRA_MODULES:
            importlib.import_module(f"{pkg.__name__}.{info.name}")
    return base._REGISTRY


class UnifiedFetchProvider:
    name = "fetch"

    def __init__(self, secrets: ProviderSecrets, client: httpx.AsyncClient) -> None:
        import_all_providers()
        self._providers: dict[str, FetchProvider] = {
            name: cls(secrets, client)
            for name, cls in base._REGISTRY.items()
            if cls.is_available(secrets)
        }

    @property
    def active_names(self) -> list[str]:
        return list(self._providers)

    async def fetch_url(self, url: str, provider: str) -> FetchResult:
        selected = self._providers.get(provider)
        if selected is None:
            raise ProviderError(ErrorType.INVALID_INPUT,
                f"Invalid provider: {provider}. Valid: {', '.join(self._providers)}",
                self.name)
        return await selected.fetch_url(url)


def get_active_fetch_providers(secrets: ProviderSecrets) -> list[str]:
    return [n for n, c in import_all_providers().items() if c.is_available(secrets)]

def has_any_fetch_provider(secrets: ProviderSecrets) -> bool:
    return any(c.is_available(secrets) for c in import_all_providers().values())


def validate_registry() -> None:
    """Fail fast if the waterfall/breaker name-contract drifts from the registry.
    Every dispatchable name (waterfall steps + breakers) MUST be registered; serpapi
    is the one allowed registered-but-unreferenced (explicit-only, overview §0.6).
    Call once at startup (doc 11 build_server)."""
    from omnifetch.fetch.engine.waterfall import BREAKERS, WATERFALL_STEPS
    registered = set(import_all_providers())
    referenced = {b.provider for b in BREAKERS}
    for step in WATERFALL_STEPS:
        referenced.update(step.providers)
    if missing := referenced - registered:
        raise RuntimeError(f"waterfall references unregistered providers: {sorted(missing)}")
    if extra := registered - referenced - {"serpapi"}:
        _LOGGER.warning("registered but never auto-selected: %s", sorted(extra))
```

**Differences from TS that simplify Python:**
- TS uses module-singleton `ToolRegistry` + `initialize_providers` idempotency
  guard (`index.ts:25-31`) because multiple Durable Objects share an isolate.
  Python has **one process** — instantiate `UnifiedFetchProvider` once in the
  server lifespan (doc 11) and inject it. No singleton, no swap-race, no
  idempotency gate needed. (Drop `providers/index.ts` entirely.)
- `active_names` order = registry **insertion order** = the providers package's
  module-import order (`pkgutil.iter_modules` → alphabetical, deterministic). The
  actual try-order is governed by the **waterfall** (doc 10), not registry order, so
  this only affects listings/error-message ordering — still deterministic per run.
- **No hand-maintained registry/config dicts**: `__init_subclass__` + the package
  auto-import replace the TS `PROVIDERS` array, and `required_secrets` replaces the
  central availability map — drop `providers/index.ts`, `PROVIDER_CONFIGS`, and
  `_TRIVIAL_KEYS` entirely.

---

## 07.3 The 19 generic providers — spec table

All under `fetch/providers/<name>.py`. Each `fetch_url`: validate key → one HTTP
call → guard empty → `FetchResult`. **Auth legend**: `Bearer`=`Authorization:
Bearer <k>`; `Q:<p>`=key in query param `<p>`; `H:<hdr>`=key in header `<hdr>`;
`Basic`=`Authorization: Basic <b64>`.

| name | method + path | auth | request body / params | content ← | title ← | metadata | src lines |
|---|---|---|---|---|---|---|---|
| **jina** | POST `{base}/` | Bearer (+`X-Return-Format: markdown`, `Accept: json`) | `{url}` | `data.data.content` | `data.data.title` | `{tokens}` if `usage.tokens` | jina:20-50 |
| **firecrawl** | POST `{base}/v2/scrape` | Bearer | `{url, formats:["markdown"], onlyMainContent:true}` | `data.data.markdown` (req `success`) | `metadata.title` | `{description,status_code}` | firecrawl:23-58 |
| **tavily** | POST `{base}/extract` | Bearer | `{urls:[url], extract_depth:"basic", format:"markdown"}` | `results[0].raw_content` (check `failed_results`) | `extract_markdown_title` | — | tavily:16-50 |
| **linkup** | POST `{base}/v1/fetch` | Bearer | `{url}` | `data.markdown` | `extract_markdown_title` | — | linkup:15-42 |
| **spider** | POST `{base}/scrape` | Bearer | `{url, return_format:"markdown"}` | `data[0].content` (array; check `page.error`) | `extract_markdown_title` | `{status}` | spider:19-58 |
| **brightdata** | POST `{base}/request` | Bearer | `{zone, url, format:"raw", data_format:"markdown"}` | **http_text** (raw md) | `extract_markdown_title` | — | brightdata:11-47 |
| **olostep** | POST `{base}/v1/scrapes` | Bearer | `{url, formats:["markdown"]}` | `data.result.markdown_content` | `extract_markdown_title` | — | olostep:14-46 |
| **you** | POST `{base}/v1/contents` | H:`X-API-Key` | `{urls:[url], formats:["markdown"]}` | `data[0].markdown` (array) | `data[0].title` | — | you:16-47 |
| **leadmagic** | POST `{base}/api/scrape` | H:`X-API-Key` | `{url}` | `data.markdown` | `data.title \|\| extract_markdown_title` | — | leadmagic:11-41 |
| **scrapingant** | GET `{base}/v2/markdown` | Q:`x-api-key` | `?url=&x-api-key=` | `data.markdown` (JSON) | `extract_markdown_title` | — | scrapingant:16-44 |
| **scrapegraphai** | POST `{base}/v1/markdownify` | H:`SGAI-APIKEY` | `{website_url:url}` | `data.result` (check `status/error`) | `extract_markdown_title` | `{request_id}` | scrapegraphai:20-54 |
| **scrapeless** | POST `{base}/api/v2/unlocker/request` | H:`x-api-token` | `{actor:"unlocker.webunlocker", input:{url,method:"GET",redirect:false,jsRender:{enabled:true,response:{type:"markdown"}}}, proxy:{country:"ANY"}}` | `data.data` (req `code==200`) | `extract_markdown_title` | — | scrapeless:17-60 |
| **scrapedo** | GET `{base}` | Q:`token` | `?token=&url=&output=markdown` | **http_text** | `extract_markdown_title` | — | scrapedo:11-27 |
| **scrapfly** | GET `{base}/scrape` | Q:`key` | `?key=&url=&format=markdown` | `data.result.content` (JSON) | `extract_markdown_title` | `{status_code}` | scrapfly:22-51 |
| **scrapingbee** | GET `{base}/api/v1` | Q:`api_key` | `?api_key=&url=&render_js=false&return_page_markdown=true` | **http_text** | `extract_markdown_title` | — | scrapingbee:11-33 |
| **scraperapi** | GET `{base}` | Q:`api_key` | `?api_key=&url=&output_format=markdown` | **http_text** | `extract_markdown_title` | — | scraperapi:11-27 |
| **oxylabs** | POST `{base}/v1/queries` | Basic `b64(user:pass)` | `{source:"universal", url, markdown:true}` | `results[0].content` | `extract_markdown_title` | — | oxylabs:11-45 |
| **decodo** | POST `{base}/v2/scrape` | Basic `<key>` (pre-encoded) | `{url, markdown:true}` | `results[0].content` | `extract_markdown_title` | — | decodo:11-44 |
| **cloudflare_browser** | POST `{base}/accounts/{acct}/browser-rendering/markdown` | H:`X-Auth-Email`+`X-Auth-Key` | `{url, rejectResourceTypes:["image","media","font"]}` | `data.result` (req `success`) | `extract_markdown_title` | — | cloudflare_browser:17-51 |

**Per-provider notes**
- `result.url`: most use the requested `url`; **jina/firecrawl/you/scrapingant**
  prefer the upstream-returned URL (`data.*.url ?? url`). Preserve per-provider.
- `brightdata.zone` comes from `secrets.bright_data_zone` (default `unblocker`).
- `oxylabs` reads BOTH `secrets.oxylabs_username`+`oxylabs_password`; build Basic
  via `basic_auth(user, pass)`.
- `decodo` key is already base64 — pass straight into `Authorization: Basic`.
- `olostep` reads `result.markdown_content` **only** — do **not** add a
  `markdown_hosted_url` fallback (it's a type-only field in the TS response with no
  runtime read; #4).
- `cloudflare_browser` builds the path from `secrets.cloudflare_account_id`; auth
  is two headers, not one key — `validate_api_key` each of the three.
- **GET providers with key in query** (scrapedo/scrapfly/scrapingbee/scraperapi/
  scrapingant): build the URL with `urllib.parse.urlencode` / `httpx`'s `params=`,
  and rely on `_redact` (doc 02) to keep the key out of logs.

### Representative full implementation (jina) — the canonical shape
```python
"""Jina Reader (r.jina.ai): URL → markdown, fast + token-efficient."""
from __future__ import annotations
from typing import Any
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key


class JinaFetchProvider(FetchProvider):
    name = "jina"
    description = "Read a URL as markdown using Jina Reader API."
    base_url = "https://r.jina.ai"
    timeout_ms = 30_000
    required_secrets = ("jina_api_key",)

    async def fetch_url(self, url: str) -> FetchResult:
        key = validate_api_key(self._secrets.jina_api_key, self.name)
        try:
            data: Any = await http_json(
                self._client, self.name, f"{self.base_url}/", method="POST",
                headers={"Authorization": f"Bearer {key}",
                         "Accept": "application/json",
                         "X-Return-Format": "markdown"},
                json={"url": url}, timeout_s=self.timeout_s)
            inner = data.get("data") or {}
            content = inner.get("content")
            if not content:
                raise ValueError("Jina Reader returned no content")
            tokens = (inner.get("usage") or {}).get("tokens")
            return FetchResult(
                url=inner.get("url") or url, title=inner.get("title") or "",
                content=content, source_provider=self.name,
                metadata={"tokens": tokens} if tokens else None)
        except Exception as exc:                # noqa: BLE001 — normalized below
            handle_provider_error(exc, self.name, "fetch URL content")
```
Note the **self-described config** (base_url/timeout_ms/required_secrets as class
attrs — nothing lands in a central dict) and httpx's **`json=`** kwarg, which
serializes the body *and* sets `Content-Type` (no manual `json.dumps`). The §07.3
table values become these class attrs.

---

## 07.4 Acceptance criteria
1. **Self-registration + guard**: importing the providers package registers all
   **28** names (identical to `unified/fetch.ts:42-69`) in `FetchProvider._REGISTRY`,
   each carrying `base_url`/`timeout_ms`/`required_secrets` per the doc-04 table; a
   **duplicate** or **empty** `name` raises `TypeError` at import. `validate_registry()`
   passes for the shipped waterfall, raises `RuntimeError` if a waterfall/breaker name
   is unregistered, and warns on any registered-but-unreferenced provider except
   `serpapi` (use a throwaway provider in a test package to exercise both).
2. **Availability gating**: with only `JINA_API_KEY` set,
   `UnifiedFetchProvider(secrets, client).active_names == ["jina"]` (driven by each
   class's `required_secrets`); dispatch to any other name raises
   `ProviderError(INVALID_INPUT, "Invalid provider...")`.
3. **Per-provider unit test** (each of the 19, via `respx`): given a recorded
   success body, `fetch_url` returns the expected `FetchResult`
   (url/title/content/source_provider/metadata) exactly per the table; given an
   empty/`success:false` body, raises `ProviderError`; given 401/429, raises the
   mapped `API_ERROR`/`RATE_LIMIT` (from doc 02).
4. **No key**: `validate_api_key(None,...)` path → `ProviderError(INVALID_INPUT)`
   (a provider with no key never enters `active_names`, so this is the dispatch
   safety net).
5. **Title fallback**: providers using `extract_markdown_title` produce the right
   `# H1`-derived title; `leadmagic` prefers `data.title` when present.
6. `result.url` uses upstream URL for jina/firecrawl/you/scrapingant, requested
   URL elsewhere.
7. `handle_provider_error` annotated `NoReturn`; mypy sees no missing-return.
8. **Explicit client DI (#6)**: providers receive the `httpx.AsyncClient` at
   construction and call `http_json(self._client, …)`; **no** provider imports
   `load_config()` or constructs its own client (guarded by the §13.5 grep test +
   a unit test injecting a `respx`-mocked client via
   `UnifiedFetchProvider(secrets, client)`).
9. `mypy --strict` + ruff (Google docstrings, 80-col) clean for every module.

## 07.5 Interfaces
**Exposes:** `FetchProvider` (base, ctor `(secrets, client)`; declares
`name`/`base_url`/`timeout_ms`/`required_secrets`; `__init_subclass__` self-registers;
`is_available` classmethod), `UnifiedFetchProvider` (ctor `(secrets, client)`),
`import_all_providers`, `validate_registry`, `get_active_fetch_providers`,
`has_any_fetch_provider`, and the 19 provider classes. **Consumes:** `fetch/shared`
(`http` `http_json/http_text/http_raw` client-first, `util`, `html`, `config`
`ProviderSecrets`, `types`), `httpx`, stdlib (`importlib`, `pkgutil`, `inspect`).
