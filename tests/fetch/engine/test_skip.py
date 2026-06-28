"""Tests for skip-provider parsing and validation."""

from __future__ import annotations

import pytest

from omnifetch.fetch.engine.skip import (
    parse_skip_providers,
    validate_skip_providers,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        (["Tavily", " firecrawl ", "", 12], ["tavily", "firecrawl"]),
        ('["tavily","firecrawl"]', ["tavily", "firecrawl"]),
        ("tavily, firecrawl", ["tavily", "firecrawl"]),
        ("tavily", ["tavily"]),
        ("null", []),
        ("undefined", []),
        ('["TAVILY", 7, ""]', ["tavily"]),
        ("[tavily, firecrawl]", ["tavily", "firecrawl"]),
        (
            f"{chr(0x201C)}[tavily, firecrawl]{chr(0x201D)}",
            ["tavily", "firecrawl"],
        ),
        ("'tavily'", ["tavily"]),
        ("", []),
        ({"provider": "tavily"}, []),
    ],
)
def test_parse_skip_providers_accepts_documented_shapes(
    raw: object,
    expected: list[str],
) -> None:
    assert parse_skip_providers(raw) == expected


def test_parse_skip_providers_caps_native_array_length() -> None:
    names = [f"provider-{index}" for index in range(70)]
    assert parse_skip_providers(names) == names[:64]


def test_parse_skip_providers_drops_oversized_items() -> None:
    assert parse_skip_providers(["tavily", "x" * 201]) == ["tavily"]


def test_parse_skip_providers_rejects_oversized_string() -> None:
    assert parse_skip_providers("x" * 4097) == []


def test_validate_skip_providers_splits_known_and_unknown() -> None:
    assert validate_skip_providers(
        ["tavily", "bogus", "firecrawl", "bogus"],
        ["firecrawl", "tavily"],
    ) == (["tavily", "firecrawl"], ["bogus", "bogus"])
