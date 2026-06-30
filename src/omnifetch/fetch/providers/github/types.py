"""Typed data containers for the GitHub fetch provider."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ParsedGitHubUrl:
    """Parsed GitHub URL route data."""

    resource_type: str
    owner: str | None = None
    repo: str | None = None
    ref: str | None = None
    path: str | None = None
    resource_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReadmeTruncation:
    """README truncation result."""

    content: str
    readme_truncated: bool
    readme_original_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class TextFile:
    """Text file content and byte size."""

    text: str
    size: int


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """Repository tree entry."""

    path: str
    type: str
    size: int | None = None


@dataclass(frozen=True, slots=True)
class RepoOwner:
    """Repository owner summary."""

    login: str
    url: str
    type: str


@dataclass(frozen=True, slots=True)
class RepoLicense:
    """Repository license summary."""

    name: str
    id: str


@dataclass(frozen=True, slots=True)
class ForkParent:
    """Repository fork parent summary."""

    name: str
    url: str


@dataclass(frozen=True, slots=True)
class RepoCommit:
    """Repository commit summary."""

    date: str
    author: str
    message: str


@dataclass(frozen=True, slots=True)
class RepoIssue:
    """Repository issue summary."""

    number: int
    title: str
    state: str
    author: str
    labels: str
    updated_at: str
    body: str


@dataclass(frozen=True, slots=True)
class RepoPullRequest:
    """Repository pull-request summary."""

    number: int
    title: str
    author: str
    labels: str
    updated_at: str
    is_draft: bool
    body: str


@dataclass(frozen=True, slots=True)
class RepoRelease:
    """Repository release summary."""

    name: str
    tag: str
    published_at: str
    is_prerelease: bool
    body: str


@dataclass(frozen=True, slots=True)
class RepoOverviewData:
    """Repository overview data used by the markdown renderer."""

    full_name: str
    description: str
    owner: RepoOwner
    license: RepoLicense | None
    visibility: str
    default_branch: str
    created_at: str
    pushed_at: str
    is_fork: bool
    is_archived: bool
    fork_parent: ForkParent | None
    disk_usage_bytes: int
    stars: int
    forks: int
    open_issues_count: int
    open_prs_count: int
    watchers: int
    star_velocity: str
    topics: list[str]
    features: str
    languages: dict[str, int]
    tree_entries: list[TreeEntry]
    docs_dir_name: str | None
    docs_files: list[str]
    ai_rules_listing: dict[str, list[TextFile]]
    ai_rules_inline: dict[str, TextFile]
    dep_configs: list[tuple[str, str]]
    readme: TextFile | None
    context_files: dict[str, TextFile] = field(default_factory=dict)
    too_large_context: list[str] = field(default_factory=list)
    extra_detected: list[str] = field(default_factory=list)
    commits: list[RepoCommit] = field(default_factory=list)
    monthly_commits: list[tuple[str, int]] = field(default_factory=list)
    issues: list[RepoIssue] = field(default_factory=list)
    pull_requests: list[RepoPullRequest] = field(default_factory=list)
    releases: list[RepoRelease] = field(default_factory=list)
    api_source: str = "rest"
    rate_limit_remaining: int | None = None
