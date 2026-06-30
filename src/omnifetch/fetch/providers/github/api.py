"""GitHub REST and GraphQL HTTP helpers."""

# ruff: noqa: E501, PLR0913

from __future__ import annotations

from typing import Any

import httpx

from omnifetch.fetch.providers.github.constants import (
    API_VERSION,
    USER_AGENT,
)
from omnifetch.fetch.shared.http import http_json, http_text


def api_headers(token: str) -> dict[str, str]:
    """Return GitHub REST API headers."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }


def raw_headers(token: str) -> dict[str, str]:
    """Return GitHub raw content API headers."""
    return {**api_headers(token), "Accept": "application/vnd.github.raw+json"}


async def github_get(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    endpoint: str,
    timeout_s: float,
) -> Any:
    """Fetch JSON from the GitHub REST API."""
    return await http_json(
        client,
        "github",
        f"{base_url}{endpoint}",
        headers=api_headers(token),
        timeout_s=timeout_s,
    )


async def github_get_raw(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    endpoint: str,
    timeout_s: float,
) -> str:
    """Fetch raw text from the GitHub REST contents API."""
    return await http_text(
        client,
        "github",
        f"{base_url}{endpoint}",
        headers=raw_headers(token),
        timeout_s=timeout_s,
    )


async def github_get_safe(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    endpoint: str,
    timeout_s: float,
) -> Any | None:
    """Fetch optional GitHub JSON, returning ``None`` on provider failure."""
    try:
        return await github_get(client, token, base_url, endpoint, timeout_s)
    except Exception:
        return None


async def github_get_raw_safe(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    endpoint: str,
    timeout_s: float,
) -> str | None:
    """Fetch optional raw GitHub text, returning ``None`` on provider failure."""
    try:
        return await github_get_raw(
            client, token, base_url, endpoint, timeout_s
        )
    except Exception:
        return None


async def github_get_starred(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    endpoint: str,
    timeout_s: float,
) -> Any | None:
    """Fetch stargazer timestamps, returning ``None`` on provider failure."""
    try:
        return await http_json(
            client,
            "github",
            f"{base_url}{endpoint}",
            headers={
                **api_headers(token),
                "Accept": "application/vnd.github.star+json",
            },
            timeout_s=timeout_s,
        )
    except Exception:
        return None


async def github_graphql(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    query: str,
    variables: dict[str, object],
    timeout_s: float,
) -> Any:
    """Run a GitHub GraphQL query."""
    return await http_json(
        client,
        "github",
        f"{base_url}/graphql",
        method="POST",
        headers={**api_headers(token), "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout_s=timeout_s,
    )
