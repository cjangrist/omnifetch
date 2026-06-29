"""Shared YouTube URL parsing for breakers and transcript providers."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")

_YOUTUBE_HOST_SUFFIX = ".youtube.com"
_YOUTUBE_VIDEO_PATH_PREFIXES = frozenset({"embed", "shorts", "live"})
_MIN_VIDEO_PATH_PARTS = 2


def is_youtube_hostname(hostname: str) -> bool:
    """Return whether ``hostname`` belongs to YouTube URL space."""
    normalized_hostname = hostname.lower().removeprefix("www.")
    return normalized_hostname in {
        "youtu.be",
        "youtube.com",
    } or normalized_hostname.endswith(_YOUTUBE_HOST_SUFFIX)


def extract_youtube_video_id(url: str) -> str | None:
    """Return the YouTube video ID embedded in ``url``, if present."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if hostname == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        return video_id or None
    if not is_youtube_hostname(hostname):
        return None

    query_video_ids = parse_qs(parsed.query).get("v", [])
    if query_video_ids and query_video_ids[0]:
        return query_video_ids[0]

    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if (
        len(path_parts) >= _MIN_VIDEO_PATH_PARTS
        and path_parts[0] in _YOUTUBE_VIDEO_PATH_PREFIXES
    ):
        return path_parts[1]
    return None
