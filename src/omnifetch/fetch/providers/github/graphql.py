"""GitHub GraphQL query builders and tree helpers."""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from omnifetch.fetch.providers.github.constants import (
    AI_RULES_DIRS,
    DEP_CONFIG_ALLOWLIST,
    MAX_TREE_CHILDREN_DIRS,
    NOISY_DIR_NAMES,
)
from omnifetch.fetch.providers.github.types import TextFile, TreeEntry


def extract_gql_blob(obj: Any, max_bytes: int) -> TextFile | None:
    """Extract a GraphQL blob if it is present and under the size cap."""
    if not isinstance(obj, dict):
        return None
    text = obj.get("text")
    byte_size = obj.get("byteSize")
    if not isinstance(text, str) or not isinstance(byte_size, int):
        return None
    return TextFile(text, byte_size) if byte_size <= max_bytes else None


def build_core_gql() -> str:
    """Build the repository overview GraphQL query."""
    return f"""
query RepoOverview($owner: String!, $repo: String!) {{
  repository(owner: $owner, name: $repo) {{
    nameWithOwner description url isArchived isFork visibility createdAt pushedAt diskUsage
    defaultBranchRef {{ name target {{ ... on Commit {{
      history(first: 10) {{ nodes {{ oid message committedDate author {{ name }} }} }}
      {_build_monthly_history_aliases()}
    }} }} }}
    parent {{ nameWithOwner url }}
    licenseInfo {{ name spdxId }}
    repositoryTopics(first: 20) {{ nodes {{ topic {{ name }} }} }}
    languages(first: 15, orderBy: {{field: SIZE, direction: DESC}}) {{ edges {{ size node {{ name }} }} totalSize }}
    stargazerCount forkCount
    watchers {{ totalCount }}
    recent_stars: stargazers(last: 30, orderBy: {{field: STARRED_AT, direction: ASC}}) {{ edges {{ starredAt }} }}
    issues(states: OPEN, first: 5, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      totalCount nodes {{ number title state author {{ login }} labels(first: 5) {{ nodes {{ name }} }} updatedAt body }}
    }}
    pullRequests(states: OPEN, first: 5, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      totalCount nodes {{ number title body updatedAt isDraft author {{ login }} labels(first: 5) {{ nodes {{ name }} }} }}
    }}
    releases(first: 3, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ nodes {{ name tagName publishedAt isPrerelease description }} }}
    hasIssuesEnabled hasWikiEnabled hasDiscussionsEnabled hasProjectsEnabled
    owner {{ login url __typename }}
    {_build_readme_aliases()}
    {_build_context_file_aliases()}
    rootTree: object(expression: "HEAD:") {{ ... on Tree {{ entries {{ name type object {{ ... on Blob {{ byteSize }} }} }} }} }}
  }}
  rateLimit {{ remaining resetAt }}
}}
"""


def build_tree_children_query(dirs: list[str]) -> str:
    """Build a depth-two tree child GraphQL query."""
    fields = [
        f'd{index}: object(expression: "HEAD:{_escape_gql_path(path)}") '
        "{ ... on Tree { entries { name type object { ... on Blob { byteSize } } } } }"
        for index, path in enumerate(dirs[:MAX_TREE_CHILDREN_DIRS])
    ]
    return (
        "query TreeChildren($owner: String!, $repo: String!) {\n"
        "  repository(owner: $owner, name: $repo) {\n    "
        + "\n    ".join(fields)
        + "\n  }\n}"
    )


def merge_tree_children(
    root_entries: list[dict[str, Any]],
    children_data: dict[str, Any] | None,
    queried_dirs: list[str] | None = None,
) -> list[TreeEntry]:
    """Merge root tree entries with one level of fetched directory children."""
    lookup_dirs = queried_dirs or [
        str(entry.get("name"))
        for entry in root_entries
        if entry.get("type") == "tree"
        and str(entry.get("name", "")).lower() not in NOISY_DIR_NAMES
    ]
    capped_lookup = lookup_dirs[:MAX_TREE_CHILDREN_DIRS]
    return [
        tree_entry
        for entry in root_entries
        for tree_entry in _merge_one_root_entry(
            entry, children_data, capped_lookup
        )
    ]


def filter_rest_tree(entries: list[TreeEntry]) -> list[TreeEntry]:
    """Filter a recursive REST tree to root and non-noisy depth-two entries."""
    return [
        entry
        for entry in entries
        if entry.path.count("/") == 0
        or (
            entry.path.count("/") == 1
            and entry.path.split("/", maxsplit=1)[0].lower()
            not in NOISY_DIR_NAMES
        )
    ]


def _build_monthly_history_aliases() -> str:
    return "\n      ".join(
        f"m2026_{month:02d}: history(first: 1) {{ totalCount }}"
        for month in range(1, 13)
    )


def _build_readme_aliases() -> str:
    names = [
        "README.md",
        "readme.md",
        "README.rst",
        "README.markdown",
        "README",
    ]
    return "\n    ".join(
        f'readme_{index}: object(expression: "HEAD:{name}") '
        "{ ... on Blob { text byteSize } }"
        for index, name in enumerate(names)
    )


def _build_context_file_aliases() -> str:
    context = [
        f'{alias}: object(expression: "HEAD:{name}") '
        "{ ... on Blob { text byteSize } }"
        for name, alias in _context_alias_map().items()
    ]
    detect = [
        f'{name.replace(".", "_").lower()}: object(expression: "HEAD:{name}") '
        "{ ... on Blob { byteSize } }"
        for name in ("CONTRIBUTING.md", "CHANGELOG.md")
    ]
    rules = [
        f'{alias}: object(expression: "HEAD:{directory_path}") '
        "{ ... on Tree { entries { name type object { ... on Blob { text byteSize } } } } }"
        for directory_path, (alias, _) in AI_RULES_DIRS.items()
    ]
    deps = [
        f'{alias}: object(expression: "HEAD:{name}") '
        "{ ... on Blob { text byteSize } }"
        for name, (alias, _) in DEP_CONFIG_ALLOWLIST.items()
    ]
    return "\n    ".join([*context, *detect, *rules, *deps])


def _context_alias_map() -> dict[str, str]:
    return {
        "CLAUDE.md": "claude_md",
        "AGENTS.md": "agents_md",
        "GEMINI.md": "gemini_md",
        "AGENT.md": "agent_md",
        "ARCHITECTURE.md": "architecture_md",
        "DEVELOPMENT.md": "development_md",
        "CONVENTIONS.md": "conventions_md",
        "REVIEW.md": "review_md",
        ".cursorrules": "cursorrules",
        ".windsurfrules": "windsurfrules",
        ".clinerules": "clinerules",
        ".goosehints": "goosehints",
        ".roorules": "roorules",
        ".continuerules": "continuerules",
        ".github/copilot-instructions.md": "copilot_md",
        ".junie/guidelines.md": "junie_guidelines",
        "llms.txt": "llms_txt",
        "llms-full.txt": "llms_full_txt",
    }


def _escape_gql_path(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _merge_one_root_entry(
    entry: dict[str, Any],
    children_data: dict[str, Any] | None,
    capped_lookup: list[str],
) -> list[TreeEntry]:
    name = str(entry.get("name", ""))
    if entry.get("type") != "tree":
        return [TreeEntry(name, "blob", _byte_size(entry.get("object")))]
    children = _tree_children(name, children_data, capped_lookup)
    return [TreeEntry(name, "tree"), *children]


def _tree_children(
    name: str,
    children_data: dict[str, Any] | None,
    capped_lookup: list[str],
) -> list[TreeEntry]:
    if children_data is None or name not in capped_lookup:
        return []
    subtree = children_data.get(f"d{capped_lookup.index(name)}")
    entries = subtree.get("entries", []) if isinstance(subtree, dict) else []
    return [
        TreeEntry(
            f"{name}/{child.get('name', '')}",
            "tree" if child.get("type") == "tree" else "blob",
            _byte_size(child.get("object"))
            if child.get("type") != "tree"
            else None,
        )
        for child in entries
        if isinstance(child, dict)
    ]


def _byte_size(obj: object) -> int | None:
    if not isinstance(obj, dict):
        return None
    byte_size = obj.get("byteSize")
    if isinstance(byte_size, int):
        return byte_size
    return None
