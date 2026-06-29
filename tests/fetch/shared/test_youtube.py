"""Tests for shared YouTube URL parsing."""

from __future__ import annotations

import pytest

from omnifetch.fetch.shared.youtube import (
    extract_youtube_video_id,
    is_youtube_hostname,
    YOUTUBE_DOMAINS,
)


def test_youtube_domains_match_breaker_source() -> None:
    assert YOUTUBE_DOMAINS == ("youtube.com", "youtu.be")


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("youtube.com", True),
        ("www.youtube.com", True),
        ("m.youtube.com", True),
        ("music.youtube.com", True),
        ("youtu.be", True),
        ("www.youtu.be", True),
        ("notyoutube.com", False),
        ("youtube.com.example.test", False),
        ("example.test", False),
    ],
)
def test_is_youtube_hostname(hostname: str, expected: bool) -> None:
    assert is_youtube_hostname(hostname) is expected


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://youtu.be/abc123", "abc123"),
        ("https://youtu.be/abc123?t=3", "abc123"),
        ("https://youtu.be/", None),
        ("https://www.youtube.com/watch?v=abc123", "abc123"),
        ("https://m.youtube.com/watch?v=mobile123", "mobile123"),
        ("https://music.youtube.com/watch?v=music123", "music123"),
        ("https://youtube.com/embed/embed123?autoplay=1", "embed123"),
        ("https://youtube.com/shorts/short123", "short123"),
        ("https://youtube.com/live/live123", "live123"),
        ("https://youtube.com/channel/channel123", None),
        ("https://example.test/watch?v=abc123", None),
        ("http://[", None),
    ],
)
def test_extract_youtube_video_id(url: str, video_id: str | None) -> None:
    assert extract_youtube_video_id(url) == video_id
