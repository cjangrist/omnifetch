"""Fetch-result quality gate.

The gate decides whether a provider returned usable page content or a blocked,
empty, paywalled, or challenge body that should continue provider failover.
The implementation is deterministic string matching with no I/O.
"""

from __future__ import annotations

from omnifetch.fetch.shared.types import FetchResult

_MIN_CONTENT_CHARS = 200
_API_NATIVE_PROVIDERS = frozenset({"github", "supadata"})
_JUNK_AMBIGUOUS_MAX_CONTENT_CHARS = 3000

_CHALLENGE_PATTERNS = (
    "cf-browser-verification",
    "challenge-platform",
    "captcha",
    "just a moment",
    "ray id",
    "checking your browser",
    "access denied",
    "enable javascript and cookies",
    "please turn javascript on",
    "one more step",
    "[chrome](https://www.google.com/chrome/",
    "does not have access to this endpoint",
)

_JUNK_TIGHT_PATTERNS = (
    "subscribe to continue reading",
    "subscribe to read",
    "create a free account to continue",
    "create an account to continue",
    "log in to read",
    "log in to continue",
    "sign in to continue",
    "sign up to continue",
    "sign up to read",
    "this content is for members only",
    "this content is for subscribers",
    "register to continue",
    "register to read",
    "unlock this article",
    "please enable javascript",
    "javascript is required",
    "javascript must be enabled",
    "this site requires javascript",
    "enable cookies to continue",
    "please enable cookies",
    "cf-browser-verification",
    "checking your browser",
    "unusual activity from your network",
    "verify you are not a robot",
    "verify you're not a robot",
    "verify you are a human",
    "verify you're a human",
    "press and hold to confirm",
    "press & hold to confirm",
    "recaptcha verification",
    "hcaptcha challenge",
)

_JUNK_AMBIGUOUS_PATTERNS = (
    "access denied",
    "before accessing",
    "security check",
    "browser security check",
    "human verification",
    "just a moment",
    "before you continue to",
    "are you a human",
    "become a member",
)


def detect_grounded_junk(content: str) -> str | None:
    """Return a junk reason string, or ``None`` for usable content."""
    if not content:
        return "empty_body"

    lower_content = content.lower()
    tight_reason = next(
        (
            f"pattern:{pattern}"
            for pattern in _JUNK_TIGHT_PATTERNS
            if pattern in lower_content
        ),
        None,
    )
    if tight_reason is not None:
        return tight_reason

    if len(content) > _JUNK_AMBIGUOUS_MAX_CONTENT_CHARS:
        return None
    return next(
        (
            f"pattern:{pattern}"
            for pattern in _JUNK_AMBIGUOUS_PATTERNS
            if pattern in lower_content
        ),
        None,
    )


def is_fetch_failure(result: FetchResult, provider: str | None = None) -> bool:
    """Return whether a provider result should be treated as failure."""
    if not result.content:
        return True
    if provider in _API_NATIVE_PROVIDERS:
        return False
    if len(result.content) < _MIN_CONTENT_CHARS:
        return True

    lower_content = result.content.lower()
    if any(pattern in lower_content for pattern in _CHALLENGE_PATTERNS):
        return True
    return detect_grounded_junk(result.content) is not None
