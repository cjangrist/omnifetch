"""Skip-provider parsing and validation for fetch requests.

LLM clients send skip-provider values in several shapes. This module accepts
native arrays, JSON array strings, comma-separated strings, and loosely quoted
single names, then validates the normalized names against active providers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from itertools import islice

_MAX_ARRAY_ENTRIES = 64
_MAX_ENTRY_CHARS = 200
_MAX_INPUT_CHARS = 4096
_SMART_QUOTES = str.maketrans(
    dict.fromkeys(map(chr, (0x2018, 0x2019, 0x201C, 0x201D)), ""),
)


def _normalize_entry(value: str) -> str:
    """Return one normalized provider name."""
    return value.strip().lower()


def _normalize_items(items: Iterable[object]) -> list[str]:
    """Normalize a bounded iterable of possible provider names."""
    normalized = (
        _normalize_entry(item)
        for item in islice(items, _MAX_ARRAY_ENTRIES)
        if isinstance(item, str) and len(item) <= _MAX_ENTRY_CHARS
    )
    return [item for item in normalized if item]


def _strip_wrappers(value: str) -> str:
    """Strip loose brackets and quote wrappers around a skip string."""
    stripped = value.translate(_SMART_QUOTES).strip()
    while (
        (stripped.startswith("[") and stripped.endswith("]"))
        or (stripped.startswith('"') and stripped.endswith('"'))
        or (stripped.startswith("'") and stripped.endswith("'"))
    ):
        stripped = stripped[1:-1].strip()
    return stripped.replace('"', "").replace("'", "")


def _parse_skip_provider_string(raw: str) -> list[str]:
    """Parse one string-valued skip-provider input."""
    if len(raw) > _MAX_INPUT_CHARS:
        return []

    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.lower() in {"null", "undefined"}:
        return []

    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return _normalize_items(parsed)

    return _normalize_items(_strip_wrappers(stripped).split(","))


def parse_skip_providers(raw: object) -> list[str]:
    """Parse user-supplied skip-provider input into normalized names."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return _normalize_items(raw)
    if isinstance(raw, str):
        return _parse_skip_provider_string(raw)
    return []


def validate_skip_providers(
    parsed: list[str],
    active_names: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Split parsed provider names into active and unknown lists."""
    active = set(active_names)
    valid = [name for name in parsed if name in active]
    unknown = [name for name in parsed if name not in active]
    return valid, unknown
