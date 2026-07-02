"""GitHub fetch provider package."""

from __future__ import annotations

from omnifetch.fetch.providers.github.provider import GitHubFetchProvider
from omnifetch.fetch.providers.github.url_parser import parse_github_url

__all__ = ["GitHubFetchProvider", "parse_github_url"]
