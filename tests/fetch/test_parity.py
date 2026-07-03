"""TS-parity corpora for fetch behavior that must stay deterministic."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from omnifetch.fetch.engine.failure import is_fetch_failure
from omnifetch.fetch.engine.skip import parse_skip_providers
from omnifetch.fetch.engine.waterfall import BREAKERS, WATERFALL_STEPS
from omnifetch.fetch.shared.types import FetchResult


@dataclass(frozen=True, slots=True)
class SkipProviderParityCase:
    """One skip-provider parser parity vector."""

    name: str
    raw: object
    expected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FetchFailureParityCase:
    """One fetch failure-gate parity vector."""

    name: str
    provider: str | None
    content: str
    expected_failure: bool


_CANONICAL_WATERFALL_PROVIDER_ORDER = (
    "tavily",
    "firecrawl",
    "kimi",
    "linkup",
    "cloudflare_browser",
    "diffbot",
    "olostep",
    "scrapfly",
    "scrapedo",
    "decodo",
    "zyte",
    "brightdata",
    "jina",
    "spider",
    "you",
    "scrapeless",
    "scrapingbee",
    "scrapegraphai",
    "scrappey",
    "scrapingant",
    "oxylabs",
    "scraperapi",
    "leadmagic",
    "opengraph",
)

_CANONICAL_BREAKER_PROVIDERS = ("github", "supadata", "sociavault")

_SKIP_PROVIDER_PARITY_CASES = (
    SkipProviderParityCase("missing", None, ()),
    SkipProviderParityCase(
        "native-array",
        ["Tavily", " firecrawl "],
        (
            "tavily",
            "firecrawl",
        ),
    ),
    SkipProviderParityCase(
        "json-array",
        '["tavily","firecrawl"]',
        (
            "tavily",
            "firecrawl",
        ),
    ),
    SkipProviderParityCase(
        "comma-string",
        "tavily, firecrawl",
        (
            "tavily",
            "firecrawl",
        ),
    ),
    SkipProviderParityCase(
        "loose-array",
        "[tavily, firecrawl]",
        (
            "tavily",
            "firecrawl",
        ),
    ),
    SkipProviderParityCase("quoted-single", "'TAVILY'", ("tavily",)),
    SkipProviderParityCase("nullish-string", "undefined", ()),
    SkipProviderParityCase("wrong-type", {"provider": "tavily"}, ()),
)

_GOOD_ARTICLE = "# Article\n\n" + (
    "This public article body contains useful page content. " * 12
)
_LONG_AMBIGUOUS_ARTICLE = ("A" * 2500) + " access denied " + ("B" * 2500)

_FETCH_FAILURE_PARITY_CASES = (
    FetchFailureParityCase("empty-body", "tavily", "", True),
    FetchFailureParityCase("short-non-native", "tavily", "short", True),
    FetchFailureParityCase("short-github", "github", "short", False),
    FetchFailureParityCase("short-supadata", "supadata", "short", False),
    FetchFailureParityCase(
        "cloudflare-challenge",
        "firecrawl",
        ("A" * 250) + " Just a Moment " + ("B" * 20),
        True,
    ),
    FetchFailureParityCase(
        "paywall",
        "firecrawl",
        ("A" * 250) + " Subscribe to continue reading " + ("B" * 20),
        True,
    ),
    FetchFailureParityCase(
        "ambiguous-long-prose",
        "tavily",
        _LONG_AMBIGUOUS_ARTICLE,
        True,
    ),
    FetchFailureParityCase("clean-article", "tavily", _GOOD_ARTICLE, False),
)


def _waterfall_provider_order() -> tuple[str, ...]:
    """Return provider names in configured waterfall order."""
    return tuple(
        provider for step in WATERFALL_STEPS for provider in step.providers
    )


def _fetch_result(content: str, provider: str | None) -> FetchResult:
    """Return a fetch result for pure failure-gate parity tests."""
    source_provider = provider or "unknown"
    return FetchResult(
        url="https://example.test/article",
        title="Example",
        content=content,
        source_provider=source_provider,
    )


def test_provider_selection_topology_matches_ts_parity_ledger() -> None:
    """The Python topology preserves the TS breaker/waterfall provider order."""
    assert tuple(breaker.provider for breaker in BREAKERS) == (
        _CANONICAL_BREAKER_PROVIDERS
    )
    assert _waterfall_provider_order() == _CANONICAL_WATERFALL_PROVIDER_ORDER
    assert "serpapi" not in _waterfall_provider_order()


@pytest.mark.parametrize(
    "case",
    _SKIP_PROVIDER_PARITY_CASES,
    ids=lambda case: case.name,
)
def test_skip_provider_parser_matches_ts_parity_corpus(
    case: SkipProviderParityCase,
) -> None:
    """Skip-provider parser outputs match the pinned TS-compatible corpus."""
    assert tuple(parse_skip_providers(case.raw)) == case.expected


@pytest.mark.parametrize(
    "case",
    _FETCH_FAILURE_PARITY_CASES,
    ids=lambda case: case.name,
)
def test_fetch_failure_gate_matches_ts_parity_corpus(
    case: FetchFailureParityCase,
) -> None:
    """Fetch failure-gate verdicts match the pinned TS-compatible corpus."""
    assert (
        is_fetch_failure(
            _fetch_result(case.content, case.provider), case.provider
        )
        is case.expected_failure
    )
