"""Tests for fetch waterfall topology and breaker matching."""

from __future__ import annotations

import pytest

from omnifetch.fetch.engine.waterfall import (
    Breaker,
    BREAKERS,
    matches_breaker,
    Step,
    WATERFALL_STEPS,
)


def test_breaker_topology_matches_authoritative_order() -> None:
    expected = (
        Breaker(
            name="github",
            provider="github",
            domains=(
                "github.com",
                "gist.github.com",
                "raw.githubusercontent.com",
            ),
        ),
        Breaker(
            name="youtube",
            provider="supadata",
            domains=("youtube.com", "youtu.be"),
        ),
        Breaker(
            name="social_media",
            provider="sociavault",
            domains=(
                "tiktok.com",
                "instagram.com",
                "youtube.com",
                "youtu.be",
                "facebook.com",
                "fb.com",
                "twitter.com",
                "x.com",
                "pinterest.com",
                "reddit.com",
                "threads.net",
                "snapchat.com",
            ),
        ),
    )
    assert expected == BREAKERS


def test_waterfall_topology_matches_authoritative_order() -> None:
    expected = (
        Step(kind="solo", providers=("tavily",)),
        Step(kind="solo", providers=("firecrawl",)),
        Step(kind="solo", providers=("kimi",)),
        Step(kind="parallel", providers=("linkup", "cloudflare_browser")),
        Step(kind="parallel", providers=("diffbot", "olostep")),
        Step(kind="parallel", providers=("scrapfly", "scrapedo", "decodo")),
        Step(kind="solo", providers=("zyte",)),
        Step(kind="solo", providers=("brightdata",)),
        Step(
            kind="sequential",
            providers=(
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
            ),
        ),
    )
    assert expected == WATERFALL_STEPS


def test_serpapi_is_not_auto_selected() -> None:
    provider_names = {
        provider for step in WATERFALL_STEPS for provider in step.providers
    } | {breaker.provider for breaker in BREAKERS}

    assert "serpapi" not in provider_names
    assert "supadata" in provider_names


@pytest.mark.parametrize(
    ("url", "breaker_name"),
    [
        ("https://github.com/cjangrist/omnifetch", "github"),
        ("https://api.github.com/not-a-breaker", "github"),
        ("https://gist.github.com/user/abc123", "github"),
        ("https://raw.githubusercontent.com/o/r/main/file.txt", "github"),
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://music.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://www.x.com/user/status/1", "social_media"),
        ("https://old.reddit.com/r/python/comments/1", "social_media"),
        ("https://snapchat.com/add/example", "social_media"),
        ("https://example.com/article", ""),
    ],
)
def test_matches_breaker_domains(url: str, breaker_name: str) -> None:
    matches = [
        breaker.name for breaker in BREAKERS if matches_breaker(url, breaker)
    ]
    assert (matches[0] if matches else "") == breaker_name


def test_matches_breaker_rejects_invalid_url() -> None:
    assert matches_breaker("https://[bad", BREAKERS[0]) is False
