"""GitHub resource handlers."""

# ruff: noqa: E501, PLR0913

from __future__ import annotations

from urllib.parse import quote

import httpx

from omnifetch.fetch.providers.github.api import github_get, github_get_safe
from omnifetch.fetch.providers.github.constants import (
    COMMENTS_PER_PAGE,
    LIST_PER_PAGE,
    PATCH_MAX_CHARS,
    RELEASE_BODY_MAX_CHARS,
)
from omnifetch.fetch.providers.github.formatters import (
    escape_table_cell,
    format_date,
    format_size,
)
from omnifetch.fetch.providers.github.handlers_file import (
    fetch_directory,
    fetch_file,
    fetch_raw_file,
    fetch_wiki_page,
)
from omnifetch.fetch.providers.github.repo_overview import fetch_repo_overview
from omnifetch.fetch.shared.types import FetchResult

__all__ = [
    "fetch_actions",
    "fetch_commit",
    "fetch_commit_list",
    "fetch_directory",
    "fetch_file",
    "fetch_gist",
    "fetch_issue",
    "fetch_issue_list",
    "fetch_pr_list",
    "fetch_pull_request",
    "fetch_raw_file",
    "fetch_release",
    "fetch_release_latest",
    "fetch_release_list",
    "fetch_repo_overview",
    "fetch_user_profile",
    "fetch_wiki_page",
]


async def fetch_issue(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    issue_number: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub issue."""
    issue = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/issues/{issue_number}",
        timeout_s,
    )
    comments = await github_get_safe(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments?per_page={COMMENTS_PER_PAGE}",
        timeout_s,
    )
    labels = " ".join(
        f"`{label.get('name')}`" for label in _list(issue.get("labels"))
    )
    assignees = ", ".join(
        f"@{assignee.get('login')}"
        for assignee in _list(issue.get("assignees"))
    )
    content = _issue_content(issue, labels, assignees, _list(comments))
    return FetchResult(
        url=str(issue.get("html_url", "")),
        title=f"Issue #{issue.get('number')}: {issue.get('title')} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "issue",
            "state": issue.get("state"),
            "comments_count": issue.get("comments"),
        },
    )


async def fetch_issue_list(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch open GitHub issues."""
    issues = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/issues?state=open&per_page={LIST_PER_PAGE}&sort=updated",
        timeout_s,
    )
    real_issues = [
        issue for issue in _list(issues) if "pull_request" not in issue
    ]
    note = (
        " (API returned 100 results - more may exist)"
        if len(_list(issues)) >= LIST_PER_PAGE
        else ""
    )
    rows = [_issue_list_row(issue) for issue in real_issues]
    content = (
        f"# Open Issues - {owner}/{repo}\n\n**Total shown:** {len(real_issues)}{note}\n\n| # | Title | Labels | Author | Updated |\n|---|-------|--------|--------|---------|\n"
        + "\n".join(rows)
        + "\n\n---\n*Fetched via GitHub API*\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/issues",
        title=f"Open Issues - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "issue_list", "count": len(real_issues)},
    )


async def fetch_pr_list(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch open GitHub pull requests."""
    pulls = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/pulls?state=open&per_page={LIST_PER_PAGE}&sort=updated&direction=desc",
        timeout_s,
    )
    note = (
        " (showing first 100 - more may exist)"
        if len(_list(pulls)) >= LIST_PER_PAGE
        else ""
    )
    rows = [_pr_list_row(pull_request) for pull_request in _list(pulls)]
    content = (
        f"# Open Pull Requests - {owner}/{repo}\n\n**Total shown:** {len(_list(pulls))}{note}\n\n| # | Title | Author | Draft | Updated |\n|---|-------|--------|-------|---------|\n"
        + "\n".join(rows)
        + "\n\n---\n*Fetched via GitHub API*\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/pulls",
        title=f"Open Pull Requests - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "pr_list", "count": len(_list(pulls))},
    )


async def fetch_pull_request(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    pr_number: str,
    include_files: bool,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub pull request."""
    pr = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        timeout_s,
    )
    files = (
        await github_get_safe(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/pulls/{pr_number}/files?per_page={LIST_PER_PAGE}",
            timeout_s,
        )
        if include_files
        else None
    )
    state = "merged" if pr.get("merged_at") else str(pr.get("state", ""))
    content = _pull_request_content(pr, state, _list(files), include_files)
    return FetchResult(
        url=str(pr.get("html_url", "")),
        title=f"PR #{pr.get('number')}: {pr.get('title')} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "pr_files" if include_files else "pull_request",
            "state": state,
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "changed_files": pr.get("changed_files"),
        },
    )


async def fetch_release_list(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch GitHub releases."""
    releases = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/releases?per_page=10",
        timeout_s,
    )
    content = f"# Releases - {owner}/{repo}\n\n" + (
        _release_rows(_list(releases))
        if _list(releases)
        else "_No releases published_\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/releases",
        title=f"Releases - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "release_list",
            "count": len(_list(releases)),
        },
    )


async def fetch_release(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    tag: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch one GitHub release by tag."""
    release = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/releases/tags/{quote(tag)}",
        timeout_s,
    )
    return _release_detail(release, owner, repo)


async def fetch_release_latest(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch the latest GitHub release."""
    release = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/releases/latest",
        timeout_s,
    )
    return _release_detail(release, owner, repo)


async def fetch_commit_list(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    ref: str | None,
    timeout_s: float,
) -> FetchResult:
    """Fetch recent GitHub commits."""
    query = f"?sha={quote(ref)}&per_page=30" if ref else "?per_page=30"
    commits = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/commits{query}",
        timeout_s,
    )
    rows = [_commit_list_row(commit) for commit in _list(commits)]
    content = (
        f"# Commits - {owner}/{repo}{f' ({ref})' if ref else ''}\n\n| Date | Author | SHA | Message |\n|------|--------|-----|---------|\n"
        + "\n".join(rows)
        + "\n\n---\n*Fetched via GitHub API*\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/commits{f'/{ref}' if ref else ''}",
        title=f"Commits - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "commit_list",
            "count": len(_list(commits)),
            "ref": ref,
        },
    )


async def fetch_commit(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    sha: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch one GitHub commit."""
    commit = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/commits/{sha}",
        timeout_s,
    )
    commit_obj = _dict(commit.get("commit"))
    stats = _dict(commit.get("stats"))
    content = _commit_content(
        commit, commit_obj, stats, _list(commit.get("files"))
    )
    return FetchResult(
        url=str(commit.get("html_url", "")),
        title=f"Commit {str(commit.get('sha', ''))[:7]} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "commit",
            "sha": commit.get("sha"),
            "additions": stats.get("additions"),
            "deletions": stats.get("deletions"),
        },
    )


async def fetch_user_profile(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    username: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub user or organization profile."""
    user = await github_get(
        client, token, base_url, f"/users/{username}", timeout_s
    )
    endpoint = (
        f"/orgs/{username}/repos?sort=updated&per_page={LIST_PER_PAGE}"
        if user.get("type") == "Organization"
        else f"/users/{username}/repos?sort=updated&per_page={LIST_PER_PAGE}"
    )
    repos = await github_get_safe(client, token, base_url, endpoint, timeout_s)
    content = _user_profile_content(user, _list(repos))
    return FetchResult(
        url=str(user.get("html_url", "")),
        title=f"{user.get('name') or user.get('login')} (@{user.get('login')})",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "user_profile",
            "public_repos": user.get("public_repos"),
            "followers": user.get("followers"),
        },
    )


async def fetch_gist(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    gist_id: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub gist."""
    gist = await github_get(
        client, token, base_url, f"/gists/{gist_id}", timeout_s
    )
    files = _dict(gist.get("files"))
    content = _gist_content(gist, files, gist_id)
    return FetchResult(
        url=str(gist.get("html_url", "")),
        title=f"Gist: {gist.get('description') or gist_id}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "gist", "file_count": len(files)},
    )


async def fetch_actions(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch recent GitHub Actions workflow runs."""
    runs = await github_get(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/actions/runs?per_page=10",
        timeout_s,
    )
    rows = [_action_row(run) for run in _list(runs.get("workflow_runs"))]
    content = (
        f"# Actions - {owner}/{repo}\n\n| Status | Workflow | Branch | Event | Duration | Date |\n|--------|----------|--------|-------|----------|------|\n"
        + "\n".join(rows)
        + "\n\n---\n*Fetched via GitHub API*\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/actions",
        title=f"Actions - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "actions",
            "run_count": len(_list(runs.get("workflow_runs"))),
        },
    )


def _issue_content(
    issue: dict[str, object],
    labels: str,
    assignees: str,
    comments: list[dict[str, object]],
) -> str:
    content = f"# Issue #{issue.get('number')}: {issue.get('title')}\n\n| Field | Value |\n|-------|-------|\n| State | {issue.get('state')} |\n| Author | @{_dict(issue.get('user')).get('login', 'ghost')} |\n| Created | {format_date(_str(issue.get('created_at')))} |\n| Updated | {format_date(_str(issue.get('updated_at')))} |\n"
    if issue.get("closed_at"):
        content += f"| Closed | {format_date(_str(issue.get('closed_at')))} |\n"
    content += f"| Labels | {labels or 'None'} |\n| Assignees | {assignees or 'None'} |\n| Comments | {issue.get('comments')} |\n\n---\n\n"
    if issue.get("body"):
        content += f"{issue.get('body')}\n\n"
    if comments:
        content += f"---\n\n## Comments ({len(comments)})\n\n" + "".join(
            _comment_block(comment) for comment in comments
        )
    return content


def _pull_request_content(
    pr: dict[str, object],
    state: str,
    files: list[dict[str, object]],
    include_files: bool,
) -> str:
    content = f"# PR #{pr.get('number')}: {pr.get('title')}\n\n| Field | Value |\n|-------|-------|\n| State | {state} |\n| Draft | {'Yes' if pr.get('draft') else 'No'} |\n| Author | @{_dict(pr.get('user')).get('login', 'ghost')} |\n| Created | {format_date(_str(pr.get('created_at')))} |\n"
    if pr.get("merged_at"):
        content += f"| Merged | {format_date(_str(pr.get('merged_at')))} |\n"
    content += f"| Base | `{_dict(pr.get('base')).get('ref')}` <- Head: `{_dict(pr.get('head')).get('ref')}` |\n| Files Changed | {pr.get('changed_files')} |\n| Additions | +{pr.get('additions')} |\n| Deletions | -{pr.get('deletions')} |\n\n"
    if pr.get("body"):
        content += f"---\n\n{pr.get('body')}\n\n"
    if include_files and files:
        content += f"---\n\n## Changed Files ({len(files)})\n\n" + "".join(
            _file_patch(file) for file in files
        )
    return content


def _release_detail(
    release: dict[str, object], owner: str, repo: str
) -> FetchResult:
    content = f"# Release: {release.get('name') or release.get('tag_name')}\n\n**Tag:** `{release.get('tag_name')}` | **Published:** {format_date(_str(release.get('published_at')))} | **Author:** @{_dict(release.get('author')).get('login', 'ghost')}\n\n"
    if release.get("body"):
        content += f"{release.get('body')}\n\n"
    assets = _list(release.get("assets"))
    if assets:
        content += (
            "## Assets\n\n| Name | Size | Downloads |\n|------|------|-----------|\n"
            + "\n".join(_asset_row(asset) for asset in assets)
            + "\n"
        )
    return FetchResult(
        url=str(release.get("html_url", "")),
        title=f"Release {release.get('tag_name')} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "release", "tag": release.get("tag_name")},
    )


def _commit_content(
    commit: dict[str, object],
    commit_obj: dict[str, object],
    stats: dict[str, object],
    files: list[dict[str, object]],
) -> str:
    author = _dict(commit_obj.get("author"))
    content = f"# Commit `{str(commit.get('sha', ''))[:7]}`\n\n**Message:** {commit_obj.get('message', '')}\n\n**Author:** {author.get('name', 'unknown')} <{author.get('email', '')}>\n**Date:** {format_date(_str(author.get('date')))}\n"
    if stats:
        content += f"**Stats:** +{stats.get('additions')} -{stats.get('deletions')} ({stats.get('total')} total)\n"
    content += "\n"
    if files:
        content += f"## Changed Files ({len(files)})\n\n" + "".join(
            _file_patch(file) for file in files
        )
    return content


def _user_profile_content(
    user: dict[str, object],
    repos: list[dict[str, object]],
) -> str:
    content = f"# {user.get('name') or user.get('login')}\n\n"
    if user.get("bio"):
        content += f"> {user.get('bio')}\n\n"
    content += f"| Field | Value |\n|-------|-------|\n| Username | @{user.get('login')} |\n| Type | {user.get('type')} |\n| Public Repos | {user.get('public_repos')} |\n| Followers | {user.get('followers')} |\n| Following | {user.get('following')} |\n| Member Since | {format_date(_str(user.get('created_at')))} |\n\n"
    if repos:
        content += (
            "## Repositories\n\n| Repo | Stars | Language | Description |\n|------|-------|----------|-------------|\n"
            + "\n".join(_repo_row(repo) for repo in repos)
            + "\n"
        )
    return content + "\n---\n*Fetched via GitHub API*\n"


def _gist_content(
    gist: dict[str, object],
    files: dict[str, object],
    gist_id: str,
) -> str:
    content = f"# Gist: {gist.get('description') or gist_id}\n\n**Author:** @{_dict(gist.get('owner')).get('login', 'anonymous')} | **Public:** {'Yes' if gist.get('public') else 'No'} | **Created:** {format_date(_str(gist.get('created_at')))}\n\n"
    for filename, file_data_raw in files.items():
        file_data = _dict(file_data_raw)
        lang = str(file_data.get("language") or "").lower()
        content += f"## {filename}\n\n"
        if file_data.get("truncated"):
            content += f"_File truncated ({format_size(_int(file_data.get('size')))}). Fetch from raw URL._\n\n"
        elif file_data.get("content"):
            content += f"`````{lang}\n{file_data.get('content')}\n`````\n\n"
    return content


def _release_rows(releases: list[dict[str, object]]) -> str:
    return "".join(
        f"## {release.get('name') or release.get('tag_name')} (`{release.get('tag_name')}`)\n"
        f"**Published:** {format_date(_str(release.get('published_at')))} | "
        f"**Author:** @{_dict(release.get('author')).get('login', 'ghost')}"
        f"{' | Pre-release' if release.get('prerelease') else ''}"
        f"{' | Draft' if release.get('draft') else ''}\n\n"
        f"{_truncated(_str(release.get('body')), RELEASE_BODY_MAX_CHARS)}\n---\n\n"
        for release in releases
    )


def _file_patch(file: dict[str, object]) -> str:
    patch = _str(file.get("patch"))[:PATCH_MAX_CHARS]
    suffix = (
        "\n... (truncated)"
        if len(_str(file.get("patch"))) > PATCH_MAX_CHARS
        else ""
    )
    return (
        f"### {file.get('filename')} ({file.get('status')}, +{file.get('additions')}/-{file.get('deletions')})\n\n`````diff\n{patch}{suffix}\n`````\n\n"
        if file.get("patch")
        else f"### {file.get('filename')} ({file.get('status')}, +{file.get('additions')}/-{file.get('deletions')})\n\n"
    )


def _issue_list_row(issue: dict[str, object]) -> str:
    labels = ", ".join(
        str(label.get("name")) for label in _list(issue.get("labels"))
    )
    return f"| {issue.get('number')} | {escape_table_cell(_str(issue.get('title')))} | {escape_table_cell(labels) or '-'} | @{_dict(issue.get('user')).get('login', 'ghost')} | {format_date(_str(issue.get('updated_at')))} |"


def _pr_list_row(pull_request: dict[str, object]) -> str:
    return f"| {pull_request.get('number')} | {escape_table_cell(_str(pull_request.get('title')))} | @{_dict(pull_request.get('user')).get('login', 'ghost')} | {'Yes' if pull_request.get('draft') else '-'} | {format_date(_str(pull_request.get('updated_at')))} |"


def _commit_list_row(commit: dict[str, object]) -> str:
    commit_obj = _dict(commit.get("commit"))
    author = _dict(commit_obj.get("author"))
    message = escape_table_cell(
        _str(commit_obj.get("message")).split("\n", maxsplit=1)[0][:80]
    )
    return f"| {format_date(_str(author.get('date')))} | {escape_table_cell(_str(author.get('name') or 'unknown'))} | `{_str(commit.get('sha'))[:7]}` | {message} |"


def _repo_row(repo: dict[str, object]) -> str:
    description = escape_table_cell(_str(repo.get("description"))[:60])
    return f"| [{escape_table_cell(_str(repo.get('name')))}]({repo.get('html_url')}) | {repo.get('stargazers_count')} | {repo.get('language') or '-'} | {description} |"


def _asset_row(asset: dict[str, object]) -> str:
    return f"| {escape_table_cell(_str(asset.get('name')))} | {format_size(_int(asset.get('size')))} | {asset.get('download_count')} |"


def _action_row(run: dict[str, object]) -> str:
    conclusion = str(run.get("conclusion") or run.get("status") or "")
    icon = (
        "pass"
        if conclusion == "success"
        else "FAIL"
        if conclusion == "failure"
        else conclusion
    )
    return f"| {icon} | {escape_table_cell(_str(run.get('name')))} | {escape_table_cell(_str(run.get('head_branch')))} | {run.get('event')} | - | {format_date(_str(run.get('created_at')))} |"


def _comment_block(comment: dict[str, object]) -> str:
    return f"### @{_dict(comment.get('user')).get('login', 'ghost')} - {format_date(_str(comment.get('created_at')))}\n\n{comment.get('body') or ''}\n\n---\n\n"


def _truncated(text: str, max_chars: int) -> str:
    suffix = "\n... (truncated)" if len(text) > max_chars else ""
    return f"{text[:max_chars]}{suffix}\n\n" if text else ""


def _list(value: object) -> list[dict[str, object]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
