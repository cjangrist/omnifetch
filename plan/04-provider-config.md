# 04 — Provider configuration (`fetch/shared/config.py` + extend `config.py`)

> Single source of truth for every provider's **API key env var, base URL, timeout,
> and availability rule**. Mirrors `config/env.ts:136-283` (the `config.fetch`
> object) and `:403-437` (env wiring), plus `.env.example:50-79` (the canonical
> env var **names**).

---

## 04.1 The complete provider config table (port verbatim)

Timeouts in ms (kept as ms in config, converted to seconds at the httpx boundary).
"Avail rule" = what makes the provider active (the TS `registration.key()`).

| name | env var(s) | base_url | timeout | avail rule |
|---|---|---|---|---|
| tavily | `TAVILY_API_KEY` | `https://api.tavily.com` | 30000 | key |
| firecrawl | `FIRECRAWL_API_KEY` | `https://api.firecrawl.dev` | 30000 | key |
| jina | `JINA_API_KEY` | `https://r.jina.ai` | 30000 | key |
| you | `YOU_API_KEY` | `https://ydc-index.io` | 30000 | key |
| brightdata | `BRIGHT_DATA_API_KEY` + `BRIGHT_DATA_ZONE`(=`unblocker`) | `https://api.brightdata.com` | 30000 | key |
| linkup | `LINKUP_API_KEY` | `https://api.linkup.so` | 30000 | key |
| diffbot | `DIFFBOT_TOKEN` | `https://api.diffbot.com` | 30000 | key |
| sociavault | `SOCIAVAULT_API_KEY` | `https://api.sociavault.com` | **15000** | key |
| spider | `SPIDER_CLOUD_API_TOKEN` | `https://api.spider.cloud` | 30000 | key |
| scrapfly | `SCRAPFLY_API_KEY` | `https://api.scrapfly.io` | 30000 | key |
| scrapegraphai | `SCRAPEGRAPHAI_API_KEY` | `https://api.scrapegraphai.com` | 30000 | key |
| scrapedo | `SCRAPE_DO_API_TOKEN` | `https://api.scrape.do` | 30000 | key |
| scrapeless | `SCRAPELESS_API_KEY` | `https://api.scrapeless.com` | 30000 | key |
| opengraph | `OPENGRAPH_IO_API_KEY` | `https://opengraph.io` | 30000 | key |
| scrapingbee | `SCRAPINGBEE_API_KEY` | `https://app.scrapingbee.com` | 30000 | key |
| scraperapi | `SCRAPERAPI_API_KEY` | `https://api.scraperapi.com` | 30000 | key |
| zyte | `ZYTE_API_KEY` | `https://api.zyte.com` | 30000 | key |
| scrapingant | `SCRAPINGANT_API_KEY` | `https://api.scrapingant.com` | 30000 | key |
| oxylabs | `OXYLABS_WEB_SCRAPER_USERNAME` + `OXYLABS_WEB_SCRAPER_PASSWORD` | `https://realtime.oxylabs.io` | 30000 | **both** |
| olostep | `OLOSTEP_API_KEY` | `https://api.olostep.com` | 30000 | key |
| decodo | `DECODO_WEB_SCRAPING_API_KEY` (base64 `user:pass`) | `https://scraper-api.decodo.com` | **60000** | key |
| scrappey | `SCRAPPEY_API_KEY` | `https://publisher.scrappey.com` | 30000 | key |
| leadmagic | `LEADMAGIC_API_KEY` | `https://api.web2md.app` | 30000 | key |
| cloudflare_browser | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_EMAIL` + `CLOUDFLARE_API_KEY` | *(built inline, see note)* | **45000** | **all 3** |
| serpapi | `SERPAPI_API_KEY` | `https://serpapi.com/search.json` | 30000 | key |
| supadata | `SUPADATA_API_KEY` | `https://api.supadata.ai/v1` | **60000** | key |
| github | `GITHUB_API_KEY` | `https://api.github.com` | 30000 | key |
| kimi | `KIMI_API_KEY` | `https://api.kimi.com` | **60000** | key **+ needs SCRAPFLY_API_KEY** (proxy) |

**Notes / gotchas**
- `cloudflare_browser` has **no `base_url`** in TS config — it builds
  `https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering/markdown`
  inline (`cloudflare_browser/index.ts:25`). Store the constant prefix in config.
- `decodo`'s key is **already base64-encoded** `user:pass` — used directly as
  `Authorization: Basic {key}` (`decodo/index.ts:23`). Do NOT re-encode.
- `kimi` is keyed by `KIMI_API_KEY` but **also requires `SCRAPFLY_API_KEY`** because
  it proxies through Scrapfly (`kimi/index.ts:36`, `scrapfly_proxy.ts:49`). Its
  availability should reflect this OR fail at call time with a clear message
  (TS only checks `KIMI_API_KEY`; the scrapfly key check happens inside the proxy
  → `INVALID_INPUT` at call time, which the waterfall treats as fall-through).
  Recommend: keep TS parity (avail = `KIMI_API_KEY`), let the proxy raise.
- `tavily/firecrawl/you/linkup/serpapi` keys are **shared with the search side**
  in the source; here only the fetch use matters.

---

## 04.2 Python design

### Secrets (provider-native env names, NO `OMNIFETCH_` prefix)
A dedicated `pydantic-settings` class, separate from `ServerSettings`
(`config.py:21-33`, which carries the `OMNIFETCH_` prefix). Provider keys keep
their upstream names so an existing `.env`/Doppler setup drops in unchanged.

```python
"""Fetch-provider configuration: secrets (env), static endpoints, availability.

ProviderSecrets reads provider-native env var names (no OMNIFETCH_ prefix).
PROVIDER_CONFIGS is the static base_url/timeout table. Availability mirrors each
TS registration.key() — single-key, both-keys (oxylabs), or all-three
(cloudflare_browser).
"""
from __future__ import annotations
from dataclasses import dataclass
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSecrets(BaseSettings):
    """All fetch-provider secrets, read once from the environment, frozen."""
    model_config = SettingsConfigDict(extra="ignore", frozen=True,
                                      case_sensitive=False)

    tavily_api_key: str | None = Field(default=None, validation_alias="TAVILY_API_KEY")
    firecrawl_api_key: str | None = Field(default=None, validation_alias="FIRECRAWL_API_KEY")
    jina_api_key: str | None = Field(default=None, validation_alias="JINA_API_KEY")
    you_api_key: str | None = Field(default=None, validation_alias="YOU_API_KEY")
    bright_data_api_key: str | None = Field(default=None, validation_alias="BRIGHT_DATA_API_KEY")
    bright_data_zone: str = Field(default="unblocker", validation_alias="BRIGHT_DATA_ZONE")
    linkup_api_key: str | None = Field(default=None, validation_alias="LINKUP_API_KEY")
    diffbot_token: str | None = Field(default=None, validation_alias="DIFFBOT_TOKEN")
    sociavault_api_key: str | None = Field(default=None, validation_alias="SOCIAVAULT_API_KEY")
    spider_cloud_api_token: str | None = Field(default=None, validation_alias="SPIDER_CLOUD_API_TOKEN")
    scrapfly_api_key: str | None = Field(default=None, validation_alias="SCRAPFLY_API_KEY")
    scrapegraphai_api_key: str | None = Field(default=None, validation_alias="SCRAPEGRAPHAI_API_KEY")
    scrape_do_api_token: str | None = Field(default=None, validation_alias="SCRAPE_DO_API_TOKEN")
    scrapeless_api_key: str | None = Field(default=None, validation_alias="SCRAPELESS_API_KEY")
    opengraph_io_api_key: str | None = Field(default=None, validation_alias="OPENGRAPH_IO_API_KEY")
    scrapingbee_api_key: str | None = Field(default=None, validation_alias="SCRAPINGBEE_API_KEY")
    scraperapi_api_key: str | None = Field(default=None, validation_alias="SCRAPERAPI_API_KEY")
    zyte_api_key: str | None = Field(default=None, validation_alias="ZYTE_API_KEY")
    scrapingant_api_key: str | None = Field(default=None, validation_alias="SCRAPINGANT_API_KEY")
    oxylabs_username: str | None = Field(default=None, validation_alias="OXYLABS_WEB_SCRAPER_USERNAME")
    oxylabs_password: str | None = Field(default=None, validation_alias="OXYLABS_WEB_SCRAPER_PASSWORD")
    olostep_api_key: str | None = Field(default=None, validation_alias="OLOSTEP_API_KEY")
    decodo_api_key: str | None = Field(default=None, validation_alias="DECODO_WEB_SCRAPING_API_KEY")
    scrappey_api_key: str | None = Field(default=None, validation_alias="SCRAPPEY_API_KEY")
    leadmagic_api_key: str | None = Field(default=None, validation_alias="LEADMAGIC_API_KEY")
    cloudflare_account_id: str | None = Field(default=None, validation_alias="CLOUDFLARE_ACCOUNT_ID")
    cloudflare_email: str | None = Field(default=None, validation_alias="CLOUDFLARE_EMAIL")
    cloudflare_api_key: str | None = Field(default=None, validation_alias="CLOUDFLARE_API_KEY")
    serpapi_api_key: str | None = Field(default=None, validation_alias="SERPAPI_API_KEY")
    supadata_api_key: str | None = Field(default=None, validation_alias="SUPADATA_API_KEY")
    github_api_key: str | None = Field(default=None, validation_alias="GITHUB_API_KEY")
    kimi_api_key: str | None = Field(default=None, validation_alias="KIMI_API_KEY")
```

### Static endpoint table
```python
@dataclass(frozen=True, slots=True)
class FetchProviderConfig:
    name: str
    base_url: str
    timeout_ms: int

PROVIDER_CONFIGS: dict[str, FetchProviderConfig] = {
    "tavily": FetchProviderConfig("tavily", "https://api.tavily.com", 30_000),
    # ... one entry per row of §04.1 ...
    "cloudflare_browser": FetchProviderConfig(
        "cloudflare_browser", "https://api.cloudflare.com/client/v4", 45_000),
    "kimi": FetchProviderConfig("kimi", "https://api.kimi.com", 60_000),
}
```

### Availability (mirror each `registration.key()`)
```python
_TRIVIAL_KEYS: dict[str, str] = {           # name -> secrets attr (single-key)
    "tavily": "tavily_api_key", "firecrawl": "firecrawl_api_key", ...,
    "kimi": "kimi_api_key",
}

def is_available(name: str, s: ProviderSecrets) -> bool:
    """True when a provider has the secret(s) its TS registration.key() requires."""
    if name == "oxylabs":
        return bool(s.oxylabs_username and s.oxylabs_password)
    if name == "cloudflare_browser":
        return bool(s.cloudflare_account_id and s.cloudflare_email
                    and s.cloudflare_api_key)
    attr = _TRIVIAL_KEYS.get(name)
    return bool(attr and getattr(s, attr))
```

### Wire into `AppConfig` (`config.py`)
Extend the existing frozen aggregate (`config.py:55-72`):
```python
@dataclass(frozen=True, slots=True)
class AppConfig:
    server: ServerSettings
    telemetry: TelemetrySettings
    providers: ProviderSecrets          # NEW

def load_config(**server_overrides: Any) -> AppConfig:
    return AppConfig(server=ServerSettings(**server_overrides),
                     telemetry=TelemetrySettings(),
                     providers=ProviderSecrets())     # NEW
```
`PROVIDER_CONFIGS` stays a module constant (static, never per-env). Providers
receive `(FetchProviderConfig, ProviderSecrets)` at construction (doc 07).

**Cache backend settings** (server-level, `OMNIFETCH_` prefix) go on
`ServerSettings` (`config.py:21-33`), NOT `ProviderSecrets`, since they configure
the process, not an upstream API (doc 06 §06.4):
```python
cache_backend: Literal["memory", "redis", "disk"] = "memory"
redis_url: str = ""                        # OMNIFETCH_REDIS_URL
disk_cache_path: str = ".cache/omnifetch"  # OMNIFETCH_DISK_CACHE_PATH
```
The per-host HTTP cap + transient-retry knobs (`OMNIFETCH_HTTP_LIMIT_PER_HOST`,
`OMNIFETCH_HTTP_TRANSIENT_RETRIES`, doc 02) likewise belong on `ServerSettings`.
Add all of these to the `README.md` config table + `.env.example`.

---

## 04.3 `.env.example` additions
Append the §04.1 env var names (already documented in `omnisearch/.env.example:50-79`).
Mark which are required for which providers. **Do not** require any single one —
the engine runs with whatever subset is configured (parity: `get_active_fetch_providers`).

---

## 04.4 Acceptance criteria
- With only `TAVILY_API_KEY` set, `is_available("tavily")` is True and every other
  `is_available(...)` is False.
- `oxylabs` available **iff both** username+password set; `cloudflare_browser`
  available **iff all three** set (table-driven test over partial combos).
- `bright_data_zone` defaults to `"unblocker"` and is overridable.
- `PROVIDER_CONFIGS` has exactly 28 entries; names match the registry (doc 07) 1:1.
- `ProviderSecrets()` reads from `os.environ` and is frozen (mutation raises).
- `conftest.py`'s `isolated_env` must be extended to also strip provider env vars
  so tests stay hermetic (see doc 13).
- `mypy --strict` + ruff clean.

## 04.5 Interfaces
**Exposes:** `ProviderSecrets`, `FetchProviderConfig`, `PROVIDER_CONFIGS`,
`is_available`, `provider_key`. **Consumes:** `pydantic-settings` only.
