"""Waterfall topology for fetch provider selection.

The engine checks domain breakers first, then walks the provider waterfall.
The data mirrors the authoritative runtime fetch orchestrator configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from omnifetch.fetch.shared.youtube import YOUTUBE_DOMAINS


@dataclass(frozen=True, slots=True)
class Breaker:
    """Domain-specific provider shortcut before the general waterfall."""

    name: str
    provider: str
    domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Step:
    """One provider-selection tier in the fetch waterfall."""

    kind: Literal["solo", "parallel", "sequential"]
    providers: tuple[str, ...]


BREAKERS: tuple[Breaker, ...] = (
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
        domains=YOUTUBE_DOMAINS,
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

WATERFALL_STEPS: tuple[Step, ...] = (
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


def matches_breaker(url: str, breaker: Breaker) -> bool:
    """Return whether ``url`` belongs to the breaker's domain set."""
    try:
        hostname = urlsplit(url).hostname or ""
    except ValueError:
        return False

    normalized_hostname = hostname.lower().removeprefix("www.")
    return any(
        normalized_hostname == domain
        or normalized_hostname.endswith(f".{domain}")
        for domain in breaker.domains
    )
