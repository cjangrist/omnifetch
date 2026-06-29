"""GitHub URL parser."""

# ruff: noqa: PLR0911, PLR2004

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from omnifetch.fetch.providers.github.constants import RESERVED_ROUTES
from omnifetch.fetch.providers.github.types import ParsedGitHubUrl

_GIST_ID = re.compile(r"^[0-9a-f]+$", re.IGNORECASE)
_NUMBER = re.compile(r"^\d+$")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_PATCH_SUFFIX = re.compile(r"\.(diff|patch)$")


def parse_github_url(url: str) -> ParsedGitHubUrl | None:
    """Parse a GitHub URL into a provider resource route."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    hostname = parsed.hostname.lower() if parsed.hostname else ""
    parts = [
        _decode_segment(part) for part in parsed.path.rstrip("/").split("/")
    ]
    clean_parts = [part for part in parts if part]

    if hostname == "gist.github.com":
        return _parse_gist_url(clean_parts)
    if hostname == "raw.githubusercontent.com":
        return _parse_raw_url(clean_parts)
    if hostname != "github.com":
        return None
    if clean_parts[:1] == ["orgs"] and len(clean_parts) >= 2:
        return ParsedGitHubUrl("org_profile", owner=clean_parts[1])
    if len(clean_parts) == 1:
        route = clean_parts[0].lower()
        if route in RESERVED_ROUTES:
            return None
        return ParsedGitHubUrl("user_profile", owner=clean_parts[0])
    if len(clean_parts) < 2:
        return None

    owner, repo, *rest = clean_parts
    if not rest:
        return ParsedGitHubUrl("repo_overview", owner=owner, repo=repo)
    return _parse_repo_subpath(owner, repo, rest)


def _decode_segment(segment: str) -> str:
    try:
        return unquote(segment)
    except ValueError:
        return segment


def _parse_gist_url(parts: list[str]) -> ParsedGitHubUrl | None:
    if not parts:
        return None
    gist_id = parts[1] if len(parts) >= 2 else parts[0]
    if not gist_id or _GIST_ID.fullmatch(gist_id) is None:
        return None
    owner = parts[0] if len(parts) >= 2 else None
    return ParsedGitHubUrl("gist", owner=owner, resource_id=gist_id)


def _parse_raw_url(parts: list[str]) -> ParsedGitHubUrl | None:
    if len(parts) < 2:
        return None
    owner, repo, *rest = parts
    ref = rest[0] if rest else None
    path = "/".join(rest[1:]) if len(rest) > 1 else None
    return ParsedGitHubUrl(
        "raw_file", owner=owner, repo=repo, ref=ref, path=path
    )


def _parse_repo_subpath(
    owner: str,
    repo: str,
    rest: list[str],
) -> ParsedGitHubUrl:
    head = rest[0]
    if head == "raw" and len(rest) >= 2:
        return ParsedGitHubUrl(
            "file", owner=owner, repo=repo, ref="/".join(rest[1:])
        )
    if head == "issues" and len(rest) == 1:
        return ParsedGitHubUrl("issue_list", owner=owner, repo=repo)
    if head == "issues" and len(rest) > 1 and _NUMBER.fullmatch(rest[1]):
        return ParsedGitHubUrl(
            "issue", owner=owner, repo=repo, resource_id=rest[1]
        )
    if head == "pulls" and len(rest) == 1:
        return ParsedGitHubUrl("pr_list", owner=owner, repo=repo)
    if head == "pull":
        return _parse_pull_request(owner, repo, rest)
    if head == "wiki":
        return _parse_wiki(owner, repo, rest)
    if head == "releases":
        return _parse_releases(owner, repo, rest)
    if head in {"commits", "commit"}:
        return _parse_commits(owner, repo, rest)
    if head == "actions":
        return _parse_actions(owner, repo, rest)
    if head == "compare" and len(rest) > 1:
        return ParsedGitHubUrl(
            "compare", owner=owner, repo=repo, resource_id="/".join(rest[1:])
        )
    if head == "discussions":
        return _parse_discussions(owner, repo, rest)
    if head in {"blob", "tree", "blame", "edit"}:
        return _parse_blob_tree_url(owner, repo, head, rest[1:])
    return ParsedGitHubUrl("repo_overview", owner=owner, repo=repo)


def _parse_pull_request(
    owner: str,
    repo: str,
    rest: list[str],
) -> ParsedGitHubUrl:
    pr_segment = _PATCH_SUFFIX.sub("", rest[1] if len(rest) > 1 else "")
    if _NUMBER.fullmatch(pr_segment):
        resource_type = (
            "pr_files"
            if len(rest) > 2 and rest[2] == "files"
            else "pull_request"
        )
        return ParsedGitHubUrl(
            resource_type, owner=owner, repo=repo, resource_id=pr_segment
        )
    return ParsedGitHubUrl("repo_overview", owner=owner, repo=repo)


def _parse_wiki(owner: str, repo: str, rest: list[str]) -> ParsedGitHubUrl:
    if len(rest) > 1:
        return ParsedGitHubUrl(
            "wiki_page", owner=owner, repo=repo, resource_id="/".join(rest[1:])
        )
    return ParsedGitHubUrl("wiki", owner=owner, repo=repo)


def _parse_releases(owner: str, repo: str, rest: list[str]) -> ParsedGitHubUrl:
    if len(rest) > 2 and rest[1] == "tag":
        return ParsedGitHubUrl(
            "release", owner=owner, repo=repo, resource_id="/".join(rest[2:])
        )
    if len(rest) > 1 and rest[1] == "latest":
        return ParsedGitHubUrl("release_latest", owner=owner, repo=repo)
    if len(rest) == 1:
        return ParsedGitHubUrl("release_list", owner=owner, repo=repo)
    return ParsedGitHubUrl("repo_overview", owner=owner, repo=repo)


def _parse_commits(owner: str, repo: str, rest: list[str]) -> ParsedGitHubUrl:
    if rest[0] == "commits" and len(rest) == 1:
        return ParsedGitHubUrl("commit_list", owner=owner, repo=repo)
    if rest[0] == "commits" and len(rest) > 1:
        return ParsedGitHubUrl(
            "commit_list", owner=owner, repo=repo, ref="/".join(rest[1:])
        )
    resource_id = _PATCH_SUFFIX.sub("", rest[1]) if len(rest) > 1 else None
    return ParsedGitHubUrl(
        "commit", owner=owner, repo=repo, resource_id=resource_id
    )


def _parse_actions(owner: str, repo: str, rest: list[str]) -> ParsedGitHubUrl:
    if len(rest) > 2 and rest[1] == "runs":
        return ParsedGitHubUrl(
            "action_run", owner=owner, repo=repo, resource_id=rest[2]
        )
    return ParsedGitHubUrl("actions", owner=owner, repo=repo)


def _parse_discussions(
    owner: str, repo: str, rest: list[str]
) -> ParsedGitHubUrl:
    if len(rest) > 1 and _NUMBER.fullmatch(rest[1]):
        return ParsedGitHubUrl(
            "discussion", owner=owner, repo=repo, resource_id=rest[1]
        )
    return ParsedGitHubUrl("discussion_list", owner=owner, repo=repo)


def _parse_blob_tree_url(
    owner: str,
    repo: str,
    head: str,
    ref_and_path: list[str],
) -> ParsedGitHubUrl:
    resource_type = "directory" if head == "tree" else "file"
    if not ref_and_path:
        return ParsedGitHubUrl(resource_type, owner=owner, repo=repo)
    first_segment = ref_and_path[0]
    if _FULL_SHA.fullmatch(first_segment):
        path = "/".join(ref_and_path[1:]) or None
        return ParsedGitHubUrl(
            resource_type, owner=owner, repo=repo, ref=first_segment, path=path
        )
    return ParsedGitHubUrl(
        resource_type, owner=owner, repo=repo, ref="/".join(ref_and_path)
    )
