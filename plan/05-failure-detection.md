# 05 — Failure detection (`fetch/engine/failure.py`)

> The gate that decides whether a provider's result counts as a **success** or a
> **failure** (→ failover to the next provider). Cheap, pure, and on the hot path
> for every provider attempt.
>
> Source: `fetch_orchestrator.ts:143-196` (`is_fetch_failure`, `CONFIG.failure`,
> `API_NATIVE_PROVIDERS`) + `grounded_prompts.ts:112-197` (`detect_grounded_junk`
> and its TIGHT/AMBIGUOUS pattern lists). **Only the junk-detection part** of
> `grounded_prompts.ts` is in scope — the snippet system prompt is not.

---

## 05.1 Exact semantics to preserve

### `is_fetch_failure(result, provider)` — `fetch_orchestrator.ts:174-196`
Returns True (failure) when ANY of:
1. `not result.content` → fail (`:175`). Empty/None content is always a failure.
2. **API-native bypass**: if `provider in {"github","supadata"}` → **return False
   immediately** (`:172,180`), BEFORE the length/pattern checks. These return
   genuinely short structured payloads (a 50-char gist, a short transcript) that
   must not be flagged as "blocked/empty".
3. `len(content) < 200` (`min_content_chars`, `:181`) → fail.
4. `lower(content)` contains any **challenge_pattern** (`:182-183`) → fail.
5. `detect_grounded_junk(content)` is truthy (`:194`) → fail (paywall / login /
   cookie / JS-shell bodies that pass the challenge gate).
6. else → success (`:195`).

**Order matters**: API-native bypass (2) must come before length (3) and patterns
(4/5). The challenge check (4) precedes the junk check (5).

### `CONFIG.failure.challenge_patterns` — `fetch_orchestrator.ts:145-151` (12 patterns)
Matched case-insensitively as substrings:
```
cf-browser-verification
challenge-platform
captcha
just a moment
ray id
checking your browser
access denied
enable javascript and cookies
please turn javascript on
one more step
[Chrome](https://www.google.com/chrome/
does not have access to this endpoint
```

### `detect_grounded_junk(content)` — `grounded_prompts.ts:185-197`
Returns a reason string (`"empty_body"` or `f"pattern:{p}"`) or `None`.
- `if not content: return "empty_body"`.
- For each **TIGHT** pattern (`:125-160`, ~28 patterns) in `lower(content)` →
  return `f"pattern:{p}"`. **Always fires regardless of length.**
- If `len(content) <= 3000` (`JUNK_AMBIGUOUS_MAX_CONTENT_CHARS`, `:177`): for each
  **AMBIGUOUS** pattern (`:165-175`, 9 patterns) → return `f"pattern:{p}"`.
- else `None`.

**TIGHT patterns** (`:125-160`) — paywalls + JS/cookie walls + bot challenges:
```
subscribe to continue reading | subscribe to read | create a free account to continue
create an account to continue | log in to read | log in to continue | sign in to continue
sign up to continue | sign up to read | this content is for members only
this content is for subscribers | register to continue | register to read
unlock this article | please enable javascript | javascript is required
javascript must be enabled | this site requires javascript | enable cookies to continue
please enable cookies | cf-browser-verification | checking your browser
unusual activity from your network | verify you are not a robot | verify you're not a robot
verify you are a human | verify you're a human | press and hold to confirm
press & hold to confirm | recaptcha verification | hcaptcha challenge
```
**AMBIGUOUS patterns** (`:165-175`) — gated to bodies ≤ 3000 chars:
```
access denied | before accessing | security check | browser security check
human verification | just a moment | before you continue to | are you a human
become a member
```

> Note the deliberate overlap: `checking your browser`, `cf-browser-verification`,
> `access denied`, `just a moment` appear in BOTH the orchestrator's always-fire
> `challenge_patterns` AND `detect_grounded_junk`. Because `is_fetch_failure`
> checks `challenge_patterns` first (step 4), those always fire; the AMBIGUOUS
> length-gating is effectively a no-op for them but is preserved verbatim for
> fidelity and because `detect_grounded_junk` is **also** called standalone by the
> cache write-gate logic (doc 10/06).

---

## 05.2 Python design

```python
"""Result quality gate: did a provider return real page content, or a
blocked/empty/walled body that should trigger failover?

is_fetch_failure mirrors fetch_orchestrator.ts; detect_grounded_junk mirrors the
junk-pattern detector lifted from grounded_prompts.ts. Pure string work, no I/O.
"""
from __future__ import annotations
from omnifetch.fetch.shared.types import FetchResult

_MIN_CONTENT_CHARS = 200
_API_NATIVE_PROVIDERS = frozenset({"github", "supadata"})

_CHALLENGE_PATTERNS: tuple[str, ...] = (
    "cf-browser-verification", "challenge-platform", "captcha", "just a moment",
    "ray id", "checking your browser", "access denied",
    "enable javascript and cookies", "please turn javascript on", "one more step",
    "[chrome](https://www.google.com/chrome/", "does not have access to this endpoint",
)  # NOTE: lowercased once here so matching is a plain substring test.

_JUNK_TIGHT: tuple[str, ...] = ( ... )      # 28 patterns, §05.1, all lowercase
_JUNK_AMBIGUOUS: tuple[str, ...] = ( ... )  # 9 patterns, §05.1, all lowercase
_JUNK_AMBIGUOUS_MAX = 3000


def detect_grounded_junk(content: str) -> str | None:
    """Return a junk reason ('empty_body' | 'pattern:<p>') or None."""
    if not content:
        return "empty_body"
    lower = content.lower()
    for pattern in _JUNK_TIGHT:
        if pattern in lower:
            return f"pattern:{pattern}"
    if len(content) <= _JUNK_AMBIGUOUS_MAX:
        for pattern in _JUNK_AMBIGUOUS:
            if pattern in lower:
                return f"pattern:{pattern}"
    return None


def is_fetch_failure(result: FetchResult, provider: str | None = None) -> bool:
    """True when the result is blocked/empty/walled and failover should continue."""
    if not result.content:
        return True
    if provider in _API_NATIVE_PROVIDERS:
        return False
    if len(result.content) < _MIN_CONTENT_CHARS:
        return True
    lower = result.content.lower()
    if any(p in lower for p in _CHALLENGE_PATTERNS):
        return True
    if detect_grounded_junk(result.content):
        return True
    return False
```

### Idiomatic-Python notes
- Store all patterns **pre-lowercased** so matching is a bare `in` (the TS code
  lowercases each pattern at compare time via `p.toLowerCase()`; doing it once at
  module load is faster and clearer). Verify every literal is lowercase.
- `any(p in lower for p in ...)` is the comprehension form RULE_09 #10 favors.
- Keep the three checks in the exact TS order; a parity test pins this.

---

## 05.3 Acceptance criteria
1. Empty content → failure for **any** provider, including api-native
   (`is_fetch_failure(FetchResult(content="",...), "github")` is True — step 1
   precedes the bypass).
2. `github`/`supadata` with 50-char non-empty content → **not** a failure;
   the same 50-char content under `provider="tavily"` → failure (length gate).
3. 300-char body containing `"Just a Moment"` → failure (challenge pattern,
   case-insensitive).
4. 300-char body containing `"Subscribe to continue reading"` → failure (TIGHT junk).
5. A 5000-char article that merely mentions `"access denied"` in prose →
   `detect_grounded_junk` returns None for the AMBIGUOUS list (length > 3000) —
   BUT `is_fetch_failure` still returns True because `"access denied"` is also an
   always-fire `challenge_pattern`. (Pin this exact interaction.)
6. A 2000-char body == only `"Become a member"` wall → failure (AMBIGUOUS, ≤3000).
7. A clean 1000-char markdown article → **not** a failure.
8. `detect_grounded_junk` returns the precise `pattern:<p>` reason string used by
   metrics/trace.
9. `mypy --strict` + ruff clean; pure functions, no I/O, fully deterministic.

## 05.4 Interfaces
**Exposes:** `is_fetch_failure`, `detect_grounded_junk` (+ the pattern tuples for
tests). **Consumes:** `fetch/types` only. Used by `orchestrator.py` (the gate)
and `cache.py`/`orchestrator.py` (the cache write-poison guard, doc 06/10).
