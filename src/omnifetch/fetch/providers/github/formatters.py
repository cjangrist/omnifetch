"""Pure formatting helpers for the GitHub fetch provider."""

# ruff: noqa: PLR2004

from __future__ import annotations

import re
from datetime import datetime, UTC

from omnifetch.fetch.providers.github.constants import (
    BINARY_EXTENSIONS,
    DOCS_MD_EXTENSIONS,
    README_CHAR_CAP,
    README_TOKEN_CAP,
)
from omnifetch.fetch.providers.github.types import (
    ReadmeTruncation,
    TextFile,
    TreeEntry,
)

_TRANSLATION_SUFFIX = re.compile(
    r"-(AR|CS|DA|DE|EO|ES|FA|FI|FR|GR|HU|ID|IT|JP|KR|ML|NL|NO|PL|PTBR|"
    r"RO|RU|TR|UA|VN|ZH)\.(md|mdx)$",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n\n")
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_PROVIDER_SECTION = re.compile(
    r"^## ((CLAUDE|AGENTS|GEMINI|AGENT|ARCHITECTURE|DEVELOPMENT|"
    r"CONVENTIONS|REVIEW)\.md|\.(cursorrules|windsurfrules|clinerules|"
    r"goosehints|roorules|continuerules)|\.(github|cursor|windsurf|roo|"
    r"amazonq|augment|continue|trae|agents|junie)/|(Recent Commits|Commit "
    r"Activity|Open Issues|Open Pull Requests|Recent Releases|AI Rules Files|"
    r"AI Context Files|Package Manifests|llms))"
)


def escape_table_cell(text: str) -> str:
    """Escape markdown table separators and line breaks."""
    return text.replace("|", r"\|").replace("\n", " ").replace("\r", "")


def format_size(bytes_count: int) -> str:
    """Format a byte count as B, KB, or MB."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count / (1024 * 1024):.1f} MB"


def format_date(iso: str | None) -> str:
    """Format an ISO timestamp as a date."""
    if not iso:
        return "N/A"
    return iso.split("T", maxsplit=1)[0]


def format_star_velocity(
    total_stars: int,
    recent_timestamps: list[str],
) -> str:
    """Return an approximate star velocity from recent star timestamps."""
    if total_stars == 0 or len(recent_timestamps) < 2:
        return ""
    now_ms = datetime.now(tz=UTC).timestamp() * 1000
    sorted_ms = sorted(
        datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000
        for t in recent_timestamps
    )
    days = max((now_ms - sorted_ms[0]) / (1000 * 60 * 60 * 24), 0.1)
    rate_per_day = len(sorted_ms) / days
    return _format_rate_per_day(rate_per_day)


def snippet_two_sentences(text: str | None) -> str:
    """Return at most two useful sentences from markdown text."""
    if not text:
        return ""
    clean = _COMMENT.sub("", text.replace("\r\n", "\n")).strip()
    sentences = [
        part.strip()
        for part in _SENTENCE.split(clean)
        if len(part.strip()) > 10
    ]
    return " ".join(sentences[:2])[:300].strip()


def format_language_breakdown(languages: dict[str, int]) -> str:
    """Render language byte totals as markdown table rows."""
    total = sum(languages.values())
    if total == 0:
        return "_No languages detected_"
    rows = [
        f"| {language} | {bytes_count / total * 100:.1f}% | "
        f"{format_size(bytes_count)} |"
        for language, bytes_count in sorted(
            languages.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    return "\n".join(rows)


def is_binary(filename: str) -> bool:
    """Return whether a filename has a known binary extension."""
    extension = filename.rsplit(".", maxsplit=1)[-1].lower()
    return extension in BINARY_EXTENSIONS


def is_docs_md_file(path: str) -> bool:
    """Return whether a path is a non-translation markdown docs file."""
    extension = path.rsplit(".", maxsplit=1)[-1].lower()
    return (
        extension in DOCS_MD_EXTENSIONS
        and _TRANSLATION_SUFFIX.search(path) is None
    )


def truncate_readme(content: str) -> ReadmeTruncation:
    """Truncate the README section in a generated repo overview."""
    marker = "## README\n\n"
    start = content.find(marker)
    if start == -1:
        return ReadmeTruncation(content, False)
    body_start = start + len(marker)
    readme_end = _find_readme_end(content, body_start)
    readme_text = content[body_start:readme_end]
    if len(readme_text) <= README_CHAR_CAP:
        return ReadmeTruncation(content, False)

    original_tokens = len(readme_text) // 4 + (1 if len(readme_text) % 4 else 0)
    cut = readme_text.rfind("\n", 0, README_CHAR_CAP)
    cut = README_CHAR_CAP if cut == -1 else cut
    note = (
        f"\n\n*[README truncated - showing ~{README_TOKEN_CAP:,} of "
        f"{original_tokens:,} tokens]*\n"
    )
    truncated = content[:body_start] + readme_text[:cut] + note
    truncated += _build_readme_toc(readme_text) + "\n" + content[readme_end:]
    return ReadmeTruncation(truncated, True, original_tokens)


def format_docs_listing(docs_dir: str, files: list[str]) -> str:
    """Render a documentation file listing section."""
    if not files:
        return ""
    return (
        "## Documentation Files\n\n"
        f"`{docs_dir}/` - {len(files)} markdown files:\n\n"
        "```\n" + "\n".join(files) + "\n```\n\n"
    )


def format_ai_rules_listing(dirs: dict[str, list[TextFile]]) -> str:
    """Render AI rules directory listings."""
    if not dirs:
        return ""
    sections = [
        f"`{directory_path}/` - {len(files)} file"
        f"{'s' if len(files) > 1 else ''}:\n\n```\n"
        + "\n".join(f"{file.text} ({format_size(file.size)})" for file in files)
        + "\n```"
        for directory_path, files in dirs.items()
    ]
    return "## AI Rules Files\n\n" + "\n\n".join(sections) + "\n\n"


def format_dep_configs(configs: list[tuple[str, str]]) -> str:
    """Render dependency config file snippets."""
    if not configs:
        return ""
    sections = [
        f"### {name}\n\n```{_language_for_config(name)}\n{text.rstrip()}\n```"
        for name, text in configs
    ]
    return "## Package Manifests\n\n" + "\n\n".join(sections) + "\n\n"


def format_commit_activity(monthly: list[tuple[str, int]]) -> str:
    """Render monthly commit activity table."""
    if not monthly:
        return ""
    total = sum(count for _, count in monthly)
    cutoff = _last_twelve_month_cutoff()
    last_twelve_total = sum(
        count for month, count in monthly if month >= cutoff
    )
    rows = "\n".join(f"| {month} | {count} |" for month, count in monthly)
    return (
        "## Commit Activity\n\n"
        f"**{total:,} commits in last 2 years** "
        f"({last_twelve_total:,} in last 12 months)\n\n"
        "| Month | Commits |\n|-------|---------|\n"
        f"{rows}\n\n"
    )


def format_depth2_tree(entries: list[TreeEntry]) -> str:
    """Render a repository tree limited to depth two."""
    top_dirs = sorted(
        [
            entry
            for entry in entries
            if "/" not in entry.path and entry.type == "tree"
        ],
        key=lambda entry: entry.path,
    )
    top_files = sorted(
        [
            entry
            for entry in entries
            if "/" not in entry.path and entry.type != "tree"
        ],
        key=lambda entry: entry.path,
    )
    lines = [_format_dir_tree(entry, entries) for entry in top_dirs]
    lines.extend(_format_tree_file(entry) for entry in top_files)
    return "\n".join(
        line for block in lines for line in block.split("\n") if line
    )


def _format_rate_per_day(rate_per_day: float) -> str:
    if rate_per_day >= 100:
        return f"~{round(rate_per_day)}/day (~{round(rate_per_day * 30)}/month)"
    if rate_per_day >= 10:
        return f"~{round(rate_per_day)}/day (~{round(rate_per_day * 7)}/week)"
    if rate_per_day >= 1:
        return f"~{rate_per_day:.1f}/day (~{round(rate_per_day * 7)}/week)"
    if rate_per_day >= 0.14:
        return (
            f"~{rate_per_day * 7:.1f}/week (~{round(rate_per_day * 30)}/month)"
        )
    return f"~{rate_per_day * 30:.1f}/month"


def _find_readme_end(content: str, body_start: int) -> int:
    footer_pos = content.find("---\n*Fetched via", body_start)
    readme_end = len(content) if footer_pos == -1 else footer_pos
    fence_marker = ""
    char_offset = body_start
    for line in content[body_start:].split("\n"):
        fence_marker = _next_fence_marker(fence_marker, line)
        if (
            not fence_marker
            and line.startswith("## ")
            and char_offset > body_start
            and _PROVIDER_SECTION.search(line)
        ):
            return char_offset
        char_offset += len(line) + 1
    return readme_end


def _next_fence_marker(fence_marker: str, line: str) -> str:
    fence_match = _FENCE.match(line)
    if not fence_match:
        return fence_marker
    if not fence_marker:
        return fence_match.group(1)
    return (
        ""
        if line.startswith(fence_marker) and line.strip() == fence_marker
        else fence_marker
    )


def _build_readme_toc(full_readme: str) -> str:
    headings = [
        (len(match.group(1)), match.group(2).strip(), index + 1)
        for index, line in enumerate(full_readme.split("\n"))
        if (match := re.match(r"^(#{1,3})\s+(.+)", line))
        and not match.group(2).strip().startswith(("![", "<img"))
    ]
    if not headings:
        return ""
    rows = [
        f"{'  ' * (level - 1)}- {title} (L{line_number})"
        for level, title, line_number in headings
    ]
    return "\n### Table of Contents (full README)\n\n" + "\n".join(rows) + "\n"


def _language_for_config(name: str) -> str:
    extension = name.rsplit(".", maxsplit=1)[-1].lower()
    return {"json": "json", "toml": "toml", "yaml": "yaml"}.get(extension, "")


def _last_twelve_month_cutoff() -> str:
    now = datetime.now(tz=UTC)
    month_index = now.year * 12 + now.month - 12
    year = (month_index - 1) // 12
    month = (month_index - 1) % 12 + 1
    return f"{year:04d}-{month:02d}"


def _format_dir_tree(entry: TreeEntry, entries: list[TreeEntry]) -> str:
    children = [
        child
        for child in entries
        if child.path.count("/") == 1
        and child.path.split("/", maxsplit=1)[0] == entry.path
    ]
    child_dirs = sorted(
        [child for child in children if child.type == "tree"],
        key=lambda child: child.path,
    )
    child_files = sorted(
        [child for child in children if child.type != "tree"],
        key=lambda child: child.path,
    )
    rows = [f"{entry.path}/"]
    rows.extend(
        f"  {child.path.rsplit('/', maxsplit=1)[-1]}/" for child in child_dirs
    )
    rows.extend(f"  {_format_tree_file(child)}" for child in child_files)
    return "\n".join(rows)


def _format_tree_file(entry: TreeEntry) -> str:
    suffix = f" ({format_size(entry.size)})" if entry.size else ""
    return f"{entry.path.rsplit('/', maxsplit=1)[-1]}{suffix}"
