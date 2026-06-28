# 01 — Foundations: types, errors, HTML titles, utilities

> Package: `omnifetch/fetch/{types,html,util}.py`. These are **leaf modules** with
> no intra-engine dependencies — build them first; everything imports them.
>
> Source: `common/types.ts` (61), `common/html.ts` (14), `common/utils.ts` (167).

---

## 01.1 `fetch/types.py` — result + error model

### Source
`common/types.ts:27-60`:
- `FetchResult { url, title, content, source_provider, metadata? }` (`:27-33`)
- `FetchProvider { fetch_url(url): Promise<FetchResult>; name; description }` (`:35-39`) → goes in `providers/base.py` (doc 07), not here.
- `enum ErrorType { API_ERROR, RATE_LIMIT, INVALID_INPUT, NOT_FOUND, PROVIDER_ERROR }` (`:42-48`)
- `class ProviderError extends Error { type, message, provider, details? }` (`:50-60`)

### Design

```python
"""Core fetch data + error types.

FetchResult is the single value every provider returns and the orchestrator
threads through caching, failure-gating, and the MCP response. ProviderError
carries an ErrorType the orchestrator switches on for failover vs. fast-fail.
"""
from __future__ import annotations
import enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ErrorType(enum.StrEnum):           # StrEnum (3.11+) → JSON-friendly
    API_ERROR = "API_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ProviderError(Exception):
    """Provider-attributed error with a failover-routing ErrorType."""
    def __init__(self, error_type: ErrorType, message: str,
                 provider: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.provider = provider
        self.details = details


class FetchResult(BaseModel):
    """Normalized fetched-content payload (one per successful provider call)."""
    model_config = ConfigDict(extra="forbid")
    url: str
    title: str
    content: str
    source_provider: str
    metadata: dict[str, Any] | None = None
```

### Decisions / rationale
- **`FetchResult` is a Pydantic model**, not a dataclass: it is also (a sub-shape
  of) the MCP output contract, and the orchestrator validates cached entries
  against it (replaces the hand-rolled `is_valid_cached_fetch`, see doc 06).
- **`ErrorType` = `StrEnum`** so it serializes directly and compares to strings.
- `ProviderError.error_type` (not `.type` — `type` is a builtin; avoid shadowing;
  ruff `A` flags it).
- Keep `metadata` as `dict[str,Any] | None` — providers populate heterogeneous
  metadata (tokens, status_code, author, platform…). `Any` here is permitted
  (`pyproject.toml` ignores `ANN401` only for `**kwargs`; for a typed field use
  `dict[str, Any]`, which `disallow_any_generics` allows because the generic is
  parameterized).

### Acceptance criteria
- `mypy --strict` clean.
- `FetchResult(**d)` round-trips `model_dump()` for every provider's output shape.
- `ProviderError(ErrorType.NOT_FOUND, "x", "github")` — `str(err) == "x"`,
  `err.error_type is ErrorType.NOT_FOUND`, `err.provider == "github"`.
- Constructing `FetchResult` with an unknown key raises (extra=forbid).

---

## 01.2 `fetch/html.py` — title extraction

### Source — `common/html.ts` (whole file)
```ts
extract_html_title(html)      // /<title[^>]*>([\s\S]*?)<\/title>/i, strip tags, trim
extract_markdown_title(md)    // /^#\s+(.+)/m, trim
```

### Design
```python
"""Title extraction from HTML <title> or the first markdown H1.

Pure regex — no DOM/markdown parser. Providers return clean markdown or
pre-extracted text, so heavyweight HTML parsing is never needed in-process.
"""
from __future__ import annotations
import re

_HTML_TITLE = re.compile(r"<title[^>]*>([\s\S]*?)</title>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_MD_H1 = re.compile(r"^#\s+(.+)", re.MULTILINE)


def extract_html_title(html: str) -> str:
    """Return the de-tagged, trimmed <title>, or '' when absent."""
    m = _HTML_TITLE.search(html)
    return _HTML_TAG.sub("", m.group(1)).strip() if m else ""


def extract_markdown_title(markdown: str) -> str:
    """Return the first '# ' heading text, or '' when absent."""
    m = _MD_H1.search(markdown)
    return m.group(1).strip() if m else ""
```

### Notes
- JS `[\s\S]` → Python needs explicit `re.DOTALL` semantics; here `[\s\S]` works
  verbatim and avoids `.`-newline ambiguity — keep the character class.
- JS `/m` anchors `^` per line → Python `re.MULTILINE`. Keep `^#\s+`.

### Acceptance criteria
- `extract_html_title("<TITLE>Hi <b>x</b></TITLE>") == "Hi x"`.
- `extract_markdown_title("intro\n# Real Title\nmore") == "Real Title"`.
- Both return `""` on no match (parity with `common/html.ts`).
- Used by ~16 providers (see 07/08) and the cache-miss title fallbacks.

---

## 01.3 `fetch/util.py` — shared helpers

### Source — `common/utils.ts`
Port these (fetch-path-relevant); skip the search-only ones.

| TS symbol | TS lines | Port to | Notes |
|---|---|---|---|
| `validate_api_key(key, provider)` + `normalize_api_key` | `:60-77` | `validate_api_key` | strips surrounding quotes; raises `ProviderError(INVALID_INPUT)` if missing |
| `hash_key(prefix, value)` | `:39-43` | `hash_key` | `sha256` hex; **sync** in Python (no async subtle crypto) |
| `handle_provider_error(err, provider, op)` | `:93-120` | `handle_provider_error` | re-raise `ProviderError` as-is; else wrap in `ProviderError(API_ERROR, f"Failed to {op}: {msg}")` |
| `handle_rate_limit(provider, reset?)` | `:79-91` | `handle_rate_limit` | always raises `ProviderError(RATE_LIMIT)` |
| `make_signal(timeout_ms, external?)` | `:8-22` | `provider_timeout(...)` | reshaped to an `asyncio.timeout` ctx — see 02/14 |
| `sanitize_for_log(s)` | `:34-35` | `sanitize_for_log` | strip control chars, slice 200 |
| `create_error_response(err)` | `:122-133` | `create_error_response` | `{ "error": ... }` — used by tool error path |
| `retry_with_backoff(fn, opts)` | `:142-166` | `retry_with_backoff` | **optional** (unused on fetch path); port for completeness only |
| `timing_safe_equal` / `authenticate_rest_request` | `:27-58` | optional | only if REST `/fetch` is ported (11). Use `hmac.compare_digest`. |

### Design (key functions)

```python
"""Stateless helpers shared across the fetch engine: api-key validation,
cache-key hashing, error normalization, log sanitization, and the per-provider
timeout context. No I/O, no globals."""
from __future__ import annotations
import base64
import hashlib
import re
from omnifetch.fetch.types import ErrorType, ProviderError

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WRAPPING_QUOTES = re.compile(r"""^(['"])(.*)\1$""", re.DOTALL)


def validate_api_key(key: str | None, provider: str) -> str:
    """Return a trimmed/unquoted key, or raise ProviderError(INVALID_INPUT)."""
    if not key:
        raise ProviderError(
            ErrorType.INVALID_INPUT, f"API key not found for {provider}", provider
        )
    trimmed = key.strip()
    m = _WRAPPING_QUOTES.match(trimmed)
    return m.group(2) if m else trimmed


def hash_key(prefix: str, value: str) -> str:
    """SHA-256 hex of value, prefixed (mirrors common/utils.ts hash_key)."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def basic_auth(username: str, password: str = "") -> str:
    """base64('user:pass') for Basic-auth providers (replaces TS btoa)."""
    return base64.b64encode(f"{username}:{password}".encode()).decode()


def handle_provider_error(err: Exception, provider: str, operation: str) -> None:
    """Re-raise ProviderError as-is; wrap anything else as API_ERROR. Never returns."""
    if isinstance(err, ProviderError):
        raise err
    raise ProviderError(
        ErrorType.API_ERROR, f"Failed to {operation}: {err}", provider
    ) from err


def sanitize_for_log(text: str) -> str:
    """Strip control chars and clamp to 200 chars for safe logging."""
    return _CONTROL.sub("", text)[:200]
```

### Concurrency-relevant: `provider_timeout`
`make_signal` (`utils.ts:8-22`) combines an external `AbortSignal` with a
per-provider timeout. In asyncio this is replaced by the **`asyncio.timeout()`**
context manager around the provider call; external cancellation is handled by the
orchestrator cancelling the `Task` (doc 10/14). The httpx client also carries a
hard `httpx.Timeout` as defense-in-depth (doc 02). So `util.py` exposes a thin:

```python
import asyncio, contextlib
from collections.abc import AsyncIterator

@contextlib.asynccontextmanager
async def provider_timeout(timeout_ms: int) -> AsyncIterator[None]:
    """Bound a provider attempt by its configured deadline (ms)."""
    async with asyncio.timeout(timeout_ms / 1000):
        yield
```
On expiry `asyncio.timeout` raises `TimeoutError`, which the orchestrator records
as a provider failure (parity with the TS `AbortSignal.timeout` → fetch reject).

### Acceptance criteria
- `validate_api_key(None,"p")` raises `ProviderError(INVALID_INPUT)`;
  `validate_api_key('"abc"',"p") == "abc"`; `validate_api_key("  k ","p") == "k"`.
- `hash_key("fetch:","https://x") ` equals `"fetch:" + sha256("https://x").hexdigest()`
  and is **identical** to the TS value for the same input (cross-impl test vector).
- `basic_auth("u","p")` equals TS `btoa("u:p")`; `basic_auth("key")` equals `btoa("key:")`
  (zyte case, `zyte/index.ts:35`).
- `handle_provider_error(ValueError("x"),"p","fetch")` raises
  `ProviderError(API_ERROR)` with message `"Failed to fetch: x"`.
- `async with provider_timeout(50): await asyncio.sleep(1)` raises `TimeoutError`.
- `mypy --strict` + ruff clean; every function has a Google docstring.

### Interfaces exposed
`validate_api_key, hash_key, basic_auth, handle_provider_error, handle_rate_limit,
sanitize_for_log, create_error_response, provider_timeout` (+ optional
`retry_with_backoff`, `timing_safe_equal`).

### Interfaces consumed
`fetch/types.py` only.
