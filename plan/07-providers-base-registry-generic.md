# 07 — Provider base, registry/dispatcher, and the 19 generic providers

> Source: `providers/unified/fetch.ts` (107, registry+dispatcher),
> `providers/index.ts` (110, init/availability), `server/tools.ts:35-39,51-105`
> (the singleton registry), and the 19 uniform `providers/fetch/<n>/index.ts`.

---

## 07.1 `fetch/providers/base.py` — the `FetchProvider` ABC

Source contract: `common/types.ts:35-39` (`FetchProvider`). Every provider:
`name`, `description`, `async fetch_url(url) -> FetchResult`. The FP waiver makes
this ABC the right shape.

```python
"""Abstract base for all fetch providers.

A provider owns one upstream API: it validates its key, issues one (or a few) HTTP
calls via fetch.http, and maps the response to a FetchResult. fetch_url raises
ProviderError on failure; the orchestrator decides failover from the error type.
"""
from __future__ import annotations
import abc
import httpx
from omnifetch.fetch.config import FetchProviderConfig, ProviderSecrets
from omnifetch.fetch.types import FetchResult


class FetchProvider(abc.ABC):
    name: str
    description: str

    def __init__(self, cfg: FetchProviderConfig, secrets: ProviderSecrets,
                 client: httpx.AsyncClient) -> None:
        self._cfg = cfg
        self._secrets = secrets
        self._client = client          # injected shared client (#6) — never global

    @property
    def base_url(self) -> str:
        return self._cfg.base_url

    @property
    def timeout_s(self) -> float:
        return self._cfg.timeout_ms / 1000

    @abc.abstractmethod
    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch `url` and return a FetchResult, or raise ProviderError."""
```

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

## 07.2 `fetch/registry.py` — registry + dispatcher + availability

Mirrors `unified/fetch.ts:41-107` and the init gating of `providers/index.ts`.

```python
"""Fetch-provider registry and unified dispatcher.

PROVIDER_CLASSES is the static name→class table (mirrors the TS PROVIDERS array).
UnifiedFetchProvider instantiates only the providers whose keys are configured and
dispatches fetch_url(url, name) to the right instance.
"""
from __future__ import annotations
import httpx
from omnifetch.fetch.config import (PROVIDER_CONFIGS, ProviderSecrets, is_available)
from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.providers import (tavily, firecrawl, jina, ...)  # all 28
from omnifetch.fetch.types import ErrorType, FetchResult, ProviderError

PROVIDER_CLASSES: dict[str, type[FetchProvider]] = {
    "tavily": tavily.TavilyFetchProvider,
    "firecrawl": firecrawl.FirecrawlFetchProvider,
    # ... 28 entries, EXACT names from unified/fetch.ts:42-69 ...
}

class UnifiedFetchProvider:
    name = "fetch"

    def __init__(self, secrets: ProviderSecrets,
                 client: httpx.AsyncClient) -> None:
        self._providers: dict[str, FetchProvider] = {
            n: cls(PROVIDER_CONFIGS[n], secrets, client)
            for n, cls in PROVIDER_CLASSES.items()
            if is_available(n, secrets)
        }

    @property
    def active_names(self) -> list[str]:
        return list(self._providers)

    async def fetch_url(self, url: str, provider: str) -> FetchResult:
        selected = self._providers.get(provider)
        if selected is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                f"Invalid provider: {provider}. Valid: {', '.join(self._providers)}",
                self.name)
        return await selected.fetch_url(url)


def get_active_fetch_providers(secrets: ProviderSecrets) -> list[str]:
    return [n for n in PROVIDER_CLASSES if is_available(n, secrets)]

def has_any_fetch_provider(secrets: ProviderSecrets) -> bool:
    return any(is_available(n, secrets) for n in PROVIDER_CLASSES)
```

**Differences from TS that simplify Python:**
- TS uses module-singleton `ToolRegistry` + `initialize_providers` idempotency
  guard (`index.ts:25-31`) because multiple Durable Objects share an isolate.
  Python has **one process** — instantiate `UnifiedFetchProvider` once in the
  server lifespan (doc 11) and inject it. No singleton, no swap-race, no
  idempotency gate needed. (Drop `providers/index.ts` entirely.)
- `active_names` preserves **registry order** (`unified/fetch.ts:41-70` order),
  which the orchestrator's "active set" relies on for deterministic logging.

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
from omnifetch.fetch.http import http_json
from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.types import FetchResult
from omnifetch.fetch.util import handle_provider_error, validate_api_key


class JinaFetchProvider(FetchProvider):
    name = "jina"
    description = "Read a URL as markdown using Jina Reader API."

    async def fetch_url(self, url: str) -> FetchResult:
        key = validate_api_key(self._secrets.jina_api_key, self.name)
        try:
            data: Any = await http_json(
                self._client, self.name, f"{self.base_url}/", method="POST",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json",
                         "Accept": "application/json",
                         "X-Return-Format": "markdown"},
                content='{"url": %s}' % _json(url), timeout_s=self.timeout_s)
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
(Use `json.dumps({"url": url})` for the body — shown abbreviated.)

---

## 07.4 Acceptance criteria
1. **Registry parity**: `PROVIDER_CLASSES` has all **28** names, identical strings
   to `unified/fetch.ts:42-69`; `set(PROVIDER_CLASSES) == set(PROVIDER_CONFIGS)`.
2. **Availability gating**: with only `JINA_API_KEY` set,
   `UnifiedFetchProvider(secrets).active_names == ["jina"]`; dispatch to any other
   name raises `ProviderError(INVALID_INPUT, "Invalid provider...")`.
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
**Exposes:** `FetchProvider` (base, ctor `(cfg, secrets, client)`),
`UnifiedFetchProvider` (ctor `(secrets, client)`), `PROVIDER_CLASSES`,
`get_active_fetch_providers`, `has_any_fetch_provider`, and the 19 provider
classes. **Consumes:** `fetch/http` (`http_json/http_text/http_raw`, client-first
arg), `fetch/util`, `fetch/html`, `fetch/config`, `fetch/types`, `httpx`.
