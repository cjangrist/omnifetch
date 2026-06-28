"""Tests for shared HTML and markdown title extraction."""

from __future__ import annotations

from omnifetch.fetch.shared.html import (
    extract_html_title,
    extract_markdown_title,
)


def test_extract_html_title_removes_tags_and_trims() -> None:
    assert extract_html_title("<TITLE>Hi <b>x</b></TITLE>") == "Hi x"


def test_extract_html_title_matches_across_newlines() -> None:
    html = "<html><title>\n  Hello\n  <span>World</span>\n</title></html>"
    assert extract_html_title(html) == "Hello\n  World"


def test_extract_html_title_returns_empty_string_without_match() -> None:
    assert extract_html_title("<main>No title</main>") == ""


def test_extract_markdown_title_returns_first_heading() -> None:
    assert extract_markdown_title("intro\n# Real Title\nmore") == "Real Title"


def test_extract_markdown_title_returns_empty_string_without_match() -> None:
    assert extract_markdown_title("intro\n## Not H1\nmore") == ""
