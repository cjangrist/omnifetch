"""Tests for fetch-result failure detection."""

from __future__ import annotations

import pytest

from omnifetch.fetch.engine.failure import (
    _CHALLENGE_PATTERNS,
    _JUNK_AMBIGUOUS_PATTERNS,
    _JUNK_TIGHT_PATTERNS,
    detect_grounded_junk,
    is_fetch_failure,
)
from omnifetch.fetch.shared.types import FetchResult


def _result(content: str, provider: str = "tavily") -> FetchResult:
    return FetchResult(
        url="https://example.test",
        title="Example",
        content=content,
        source_provider=provider,
    )


def test_patterns_are_pre_lowercased() -> None:
    assert all(pattern == pattern.lower() for pattern in _CHALLENGE_PATTERNS)
    assert all(pattern == pattern.lower() for pattern in _JUNK_TIGHT_PATTERNS)
    assert all(
        pattern == pattern.lower() for pattern in _JUNK_AMBIGUOUS_PATTERNS
    )


@pytest.mark.parametrize("provider", ["github", "supadata", "tavily", None])
def test_empty_content_is_always_failure(provider: str | None) -> None:
    assert is_fetch_failure(_result(""), provider) is True


@pytest.mark.parametrize("provider", ["github", "supadata"])
def test_api_native_providers_bypass_length_gate(provider: str) -> None:
    assert (
        is_fetch_failure(_result("short but valid", provider), provider)
        is False
    )


def test_short_non_native_content_is_failure() -> None:
    assert is_fetch_failure(_result("short but valid"), "tavily") is True


def test_challenge_pattern_is_case_insensitive_failure() -> None:
    content = f"{'A' * 250} Just a Moment {'B' * 20}"
    assert is_fetch_failure(_result(content), "tavily") is True


def test_tight_junk_pattern_is_failure() -> None:
    content = f"{'A' * 250} Subscribe to continue reading {'B' * 20}"
    assert is_fetch_failure(_result(content), "tavily") is True
    assert (
        detect_grounded_junk(content) == "pattern:subscribe to continue reading"
    )


def test_ambiguous_long_prose_is_not_grounded_junk() -> None:
    content = f"{'A' * 2500} access denied {'B' * 2500}"
    assert detect_grounded_junk(content) is None
    assert is_fetch_failure(_result(content), "tavily") is True


def test_ambiguous_short_wall_is_failure() -> None:
    content = f"{'A' * 1000} Become a member {'B' * 990}"
    assert detect_grounded_junk(content) == "pattern:become a member"
    assert is_fetch_failure(_result(content), "tavily") is True


def test_clean_article_is_not_failure() -> None:
    content = "# Article\n\n" + (
        "This paragraph contains useful public page content. " * 25
    )
    assert len(content) > 1000
    assert detect_grounded_junk(content) is None
    assert is_fetch_failure(_result(content), "tavily") is False


def test_detect_grounded_junk_empty_body_reason() -> None:
    assert detect_grounded_junk("") == "empty_body"
