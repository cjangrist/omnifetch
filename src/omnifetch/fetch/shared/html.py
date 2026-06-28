"""Title extraction from HTML ``<title>`` or the first markdown H1.

Providers already return clean markdown or pre-extracted text, so this module
keeps title fallback extraction regex-only and dependency-free.
"""

from __future__ import annotations

import re

_HTML_TITLE = re.compile(r"<title[^>]*>([\s\S]*?)</title>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")
_MARKDOWN_H1 = re.compile(r"^#\s+(.+)", re.MULTILINE)


def extract_html_title(html: str) -> str:
    """Return the de-tagged, trimmed ``<title>`` text, or empty string."""
    match = _HTML_TITLE.search(html)
    return _HTML_TAG.sub("", match.group(1)).strip() if match else ""


def extract_markdown_title(markdown: str) -> str:
    """Return the first markdown ``#`` heading text, or empty string."""
    match = _MARKDOWN_H1.search(markdown)
    return match.group(1).strip() if match else ""
