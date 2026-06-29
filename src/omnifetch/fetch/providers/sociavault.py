"""SociaVault fetch provider: social-media URL to markdown."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.http import http_json
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key

_API_KEY_ENV_NAME = "SOCIAVAULT_API_KEY"
_TIMEOUT_MS = 15_000


@dataclass(frozen=True, slots=True)
class _PlatformRoute:
    """SociaVault endpoint metadata for one social platform."""

    hosts: tuple[str, ...]
    platform: str
    endpoint: str
    param_name: str


@dataclass(frozen=True, slots=True)
class _DetectedRoute:
    """Matched SociaVault route and request parameter value."""

    route: _PlatformRoute
    param_value: str


class _SociaVaultResponse(BaseModel):
    """Typed subset of SociaVault scrape responses."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    credits_used: int | float | None = Field(
        default=None,
        validation_alias="creditsUsed",
    )


_PLATFORM_ROUTES = (
    _PlatformRoute(
        ("reddit.com", "www.reddit.com", "old.reddit.com"),
        "reddit",
        "/v1/scrape/reddit/post/comments",
        "url",
    ),
    _PlatformRoute(
        ("twitter.com", "www.twitter.com", "x.com", "www.x.com"),
        "twitter",
        "/v1/scrape/twitter/tweet",
        "url",
    ),
    _PlatformRoute(
        ("youtube.com", "www.youtube.com", "youtu.be"),
        "youtube",
        "/v1/scrape/youtube/video",
        "url",
    ),
    _PlatformRoute(
        ("facebook.com", "www.facebook.com", "fb.com"),
        "facebook",
        "/v1/scrape/facebook/post",
        "url",
    ),
    _PlatformRoute(
        ("instagram.com", "www.instagram.com"),
        "instagram",
        "/v1/scrape/instagram/post-info",
        "url",
    ),
    _PlatformRoute(
        ("tiktok.com", "www.tiktok.com"),
        "tiktok",
        "/v1/scrape/tiktok/video-info",
        "url",
    ),
    _PlatformRoute(
        ("linkedin.com", "www.linkedin.com"),
        "linkedin",
        "/v1/scrape/linkedin/post",
        "url",
    ),
    _PlatformRoute(
        ("threads.net", "www.threads.net"),
        "threads",
        "/v1/scrape/threads/post",
        "url",
    ),
    _PlatformRoute(
        ("pinterest.com", "www.pinterest.com"),
        "pinterest",
        "/v1/scrape/pinterest/pin",
        "url",
    ),
)
_ROUTES_BY_HOST = {
    host: route for route in _PLATFORM_ROUTES for host in route.hosts
}
_SUPPORTED_PLATFORMS = ", ".join(
    dict.fromkeys(route.platform for route in _PLATFORM_ROUTES)
)


def _detect_route(url: str) -> _DetectedRoute | None:
    """Return the SociaVault route for ``url``, if supported."""
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None

    route = _ROUTES_BY_HOST.get(hostname)
    if route is None:
        return None
    return _DetectedRoute(route, url)


def _hostname_for_error(url: str) -> str:
    """Return a display hostname for unsupported URL errors."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _title_case_key(key: str) -> str:
    """Return a display label for a provider response key."""
    return " ".join(word.capitalize() for word in key.replace("_", " ").split())


def _stringify_social_value(value: Any) -> str:
    """Return SociaVault field values as markdown-safe plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_stringify_social_value(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2)
    return ""


def _format_social_content(platform: str, data: dict[str, Any]) -> str:
    """Return markdown content for one SociaVault platform payload."""
    lines = [f"# {platform} content\n"]
    lines.extend(
        f"**{_title_case_key(key)}:** {_stringify_social_value(value)}\n"
        for key, value in data.items()
        if value is not None
    )
    return "\n".join(lines)


class SociaVaultFetchProvider(FetchProvider):
    """Fetch social-media content using SociaVault API."""

    name = "sociavault"
    description = (
        "Fetch social media content using SociaVault API. Supports Reddit, "
        "Twitter/X, Instagram, TikTok, YouTube, LinkedIn, Facebook, Threads, "
        "and Pinterest."
    )
    base_url = "https://api.sociavault.com"
    timeout_ms = _TIMEOUT_MS
    required_secrets = (_API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through SociaVault and return normalized markdown."""
        api_key = validate_api_key(
            self._secrets.get(_API_KEY_ENV_NAME),
            self.name,
        )
        detected = _detect_route(url)
        if detected is None:
            hostname = _hostname_for_error(url)
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                "SociaVault only supports social media URLs "
                f"({_SUPPORTED_PLATFORMS}). Got: {hostname}",
                self.name,
            )

        try:
            query = urlencode({detected.route.param_name: detected.param_value})
            request_url = f"{self.base_url}{detected.route.endpoint}?{query}"
            data = await http_json(
                self._client,
                self.name,
                request_url,
                model=_SociaVaultResponse,
                headers={"X-API-Key": api_key},
                timeout_s=self.timeout_s,
            )
            if not data.success:
                raise ValueError("SociaVault returned unsuccessful response")

            content = _format_social_content(
                detected.route.platform,
                data.data,
            )
            return FetchResult(
                url=url,
                title=f"{detected.route.platform} content",
                content=content,
                source_provider=self.name,
                metadata={
                    "platform": detected.route.platform,
                    "credits_used": data.credits_used,
                },
            )
        except Exception as error:
            handle_provider_error(
                error,
                self.name,
                "fetch social media content",
            )
