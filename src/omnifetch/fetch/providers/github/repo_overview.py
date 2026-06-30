"""GitHub repository overview handlers."""

# ruff: noqa: E501, PLR0913

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from omnifetch.fetch.providers.github.api import (
    github_get,
    github_get_raw_safe,
    github_get_safe,
    github_get_starred,
    github_graphql,
)
from omnifetch.fetch.providers.github.constants import (
    AI_RULES_DIRS,
    AI_RULES_INLINE_MAX_BYTES,
    CONTEXT_FILE_LIMITS,
    CONTEXT_FILE_NAMES,
    DEP_CONFIG_ALLOWLIST,
    DOCS_DIR_NAMES,
    OVERVIEW_COMMITS_PER_PAGE,
    OVERVIEW_ISSUES_PER_PAGE,
    OVERVIEW_PRS_PER_PAGE,
    OVERVIEW_RELEASES_PER_PAGE,
    README_MAX_BYTES,
    STARGAZER_MAX_PAGE,
)
from omnifetch.fetch.providers.github.formatters import (
    format_size,
    format_star_velocity,
    is_docs_md_file,
)
from omnifetch.fetch.providers.github.graphql import (
    build_core_gql,
    build_tree_children_query,
    extract_gql_blob,
    filter_rest_tree,
    merge_tree_children,
)
from omnifetch.fetch.providers.github.markdown_builder import (
    build_repo_overview_result,
)
from omnifetch.fetch.providers.github.types import (
    ForkParent,
    RepoCommit,
    RepoIssue,
    RepoLicense,
    RepoOverviewData,
    RepoOwner,
    RepoPullRequest,
    RepoRelease,
    TextFile,
    TreeEntry,
)
from omnifetch.fetch.shared.types import FetchResult


async def fetch_docs_tree(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    docs_dir: str,
    tree_sha_or_branch: str,
    timeout_s: float,
) -> list[str]:
    """Fetch markdown files under the repository docs directory."""
    endpoint = f"/repos/{owner}/{repo}/git/trees/{tree_sha_or_branch}:{docs_dir}?recursive=1"
    tree_data = await github_get_safe(
        client, token, base_url, endpoint, timeout_s
    )
    tree_entries = _list_value(tree_data, "tree")
    return [
        str(entry.get("path"))
        for entry in tree_entries
        if entry.get("type") == "blob"
        and is_docs_md_file(str(entry.get("path", "")))
    ]


async def fetch_repo_overview(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a repository overview through GraphQL with REST fallback."""
    try:
        return await fetch_repo_overview_gql(
            client, token, base_url, owner, repo, timeout_s
        )
    except Exception:
        return await fetch_repo_overview_rest(
            client, token, base_url, owner, repo, timeout_s
        )


async def fetch_repo_overview_gql(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a repository overview using the GitHub GraphQL API."""
    gql = await github_graphql(
        client,
        token,
        base_url,
        build_core_gql(),
        {"owner": owner, "repo": repo},
        timeout_s,
    )
    errors = _list_value(gql, "errors")
    if errors:
        raise ValueError(
            "; ".join(str(error.get("message", "")) for error in errors)
        )
    repository = _dict_value(_dict_value(gql, "data"), "repository")
    if not repository:
        raise ValueError("Repository not found via GraphQL")
    rate = _dict_value(_dict_value(gql, "data"), "rateLimit")
    default_branch = _default_branch_from_gql(repository)
    tree_entries, docs_dir, docs_files = await _fetch_gql_tree_and_docs(
        client,
        token,
        base_url,
        owner,
        repo,
        repository,
        default_branch,
        timeout_s,
    )
    data = _repo_data_from_gql(
        repository,
        rate,
        default_branch,
        tree_entries,
        docs_dir,
        docs_files,
    )
    return build_repo_overview_result(data)


async def fetch_repo_overview_rest(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a repository overview using GitHub REST endpoints."""
    repo_data = await github_get(
        client, token, base_url, f"/repos/{owner}/{repo}", timeout_s
    )
    readme_raw = await github_get_raw_safe(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/readme",
        timeout_s,
    )
    languages = await github_get_safe(
        client,
        token,
        base_url,
        f"/repos/{owner}/{repo}/languages",
        timeout_s,
    )
    data = await _rest_overview_enrichment(
        client,
        token,
        base_url,
        owner,
        repo,
        repo_data,
        readme_raw,
        languages,
        timeout_s,
    )
    return build_repo_overview_result(data)


async def _fetch_gql_tree_and_docs(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    repository: dict[str, Any],
    default_branch: str,
    timeout_s: float,
) -> tuple[list[TreeEntry], str | None, list[str]]:
    root_entries = _list_value(_dict_value(repository, "rootTree"), "entries")
    queryable_dirs = [
        str(entry.get("name"))
        for entry in root_entries
        if entry.get("type") == "tree"
    ]
    children = await _fetch_tree_children(
        client,
        token,
        base_url,
        owner,
        repo,
        queryable_dirs,
        timeout_s,
    )
    docs_dir = next(
        (
            str(entry.get("name"))
            for entry in root_entries
            if entry.get("type") == "tree"
            and str(entry.get("name", "")).lower() in DOCS_DIR_NAMES
        ),
        None,
    )
    docs_files = (
        await fetch_docs_tree(
            client,
            token,
            base_url,
            owner,
            repo,
            docs_dir,
            default_branch,
            timeout_s,
        )
        if docs_dir
        else []
    )
    return (
        merge_tree_children(root_entries, children, queryable_dirs),
        docs_dir,
        docs_files,
    )


async def _fetch_tree_children(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    queryable_dirs: list[str],
    timeout_s: float,
) -> dict[str, Any] | None:
    if not queryable_dirs:
        return None
    try:
        data = await github_graphql(
            client,
            token,
            base_url,
            build_tree_children_query(queryable_dirs),
            {"owner": owner, "repo": repo},
            timeout_s,
        )
    except Exception:
        return None
    return _dict_value(_dict_value(data, "data"), "repository")


async def _rest_overview_enrichment(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    repo_data: Any,
    readme_raw: str | None,
    languages: Any,
    timeout_s: float,
) -> RepoOverviewData:
    repo_mapping = _dict_value(repo_data)
    default_branch = str(repo_mapping.get("default_branch") or "main")
    total_stars = _int_value(repo_data, "stargazers_count")
    star_page = min(STARGAZER_MAX_PAGE, max(1, (total_stars + 29) // 30))
    commits, issues, pulls, releases, tree_data, stars = await asyncio.gather(
        github_get_safe(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/commits?sha={default_branch}&per_page={OVERVIEW_COMMITS_PER_PAGE}",
            timeout_s,
        ),
        github_get_safe(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/issues?state=open&per_page={OVERVIEW_ISSUES_PER_PAGE}&sort=updated",
            timeout_s,
        ),
        github_get_safe(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/pulls?state=open&per_page={OVERVIEW_PRS_PER_PAGE}&sort=updated&direction=desc",
            timeout_s,
        ),
        github_get_safe(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/releases?per_page={OVERVIEW_RELEASES_PER_PAGE}",
            timeout_s,
        ),
        github_get_safe(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1",
            timeout_s,
        ),
        github_get_starred(
            client,
            token,
            base_url,
            f"/repos/{owner}/{repo}/stargazers?per_page=30&page={star_page}",
            timeout_s,
        )
        if total_stars
        else asyncio.sleep(0, result=None),
    )
    return await _repo_data_from_rest(
        client,
        token,
        base_url,
        repo_data,
        readme_raw,
        languages,
        commits,
        issues,
        pulls,
        releases,
        tree_data,
        stars,
        timeout_s,
    )


async def _repo_data_from_rest(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    repo_data: Any,
    readme_raw: str | None,
    languages: Any,
    commits: Any,
    issues: Any,
    pulls: Any,
    releases: Any,
    tree_data: Any,
    stars: Any,
    timeout_s: float,
) -> RepoOverviewData:
    full_tree, tree_paths, docs_dir, docs_files = _process_rest_tree(tree_data)
    repo_mapping = _dict_value(repo_data)
    owner_login = str(_dict_value(repo_data, "owner").get("login", ""))
    repo_name = str(repo_mapping.get("name", ""))
    context_files, dep_configs = await _fetch_rest_context_and_deps(
        client,
        token,
        base_url,
        owner_login,
        repo_name,
        tree_paths,
        timeout_s,
    )
    ai_listing, ai_inline = await _fetch_rest_ai_rules(
        client,
        token,
        base_url,
        owner_login,
        repo_name,
        full_tree,
        timeout_s,
    )
    return _rest_data(
        repo_data,
        readme_raw,
        languages,
        commits,
        issues,
        pulls,
        releases,
        stars,
        filter_rest_tree(full_tree),
        tree_paths,
        docs_dir,
        docs_files,
        context_files,
        ai_listing,
        ai_inline,
        dep_configs,
    )


def _repo_data_from_gql(
    repository: dict[str, Any],
    rate: dict[str, Any],
    default_branch: str,
    tree_entries: list[TreeEntry],
    docs_dir: str | None,
    docs_files: list[str],
) -> RepoOverviewData:
    context, too_large = _extract_gql_context_files(repository)
    ai_listing, ai_inline = _extract_gql_ai_rules(repository)
    return RepoOverviewData(
        full_name=str(repository.get("nameWithOwner", "")),
        description=str(repository.get("description") or "_No description_"),
        owner=_gql_owner(repository),
        license=_gql_license(repository),
        visibility=str(repository.get("visibility", "")),
        default_branch=default_branch,
        created_at=str(repository.get("createdAt", "")),
        pushed_at=str(repository.get("pushedAt", "")),
        is_fork=bool(repository.get("isFork")),
        is_archived=bool(repository.get("isArchived")),
        fork_parent=_gql_parent(repository),
        disk_usage_bytes=_int_value(repository, "diskUsage") * 1024,
        stars=_int_value(repository, "stargazerCount"),
        forks=_int_value(repository, "forkCount"),
        open_issues_count=_int_value(
            _dict_value(repository, "issues"), "totalCount"
        ),
        open_prs_count=_int_value(
            _dict_value(repository, "pullRequests"), "totalCount"
        ),
        watchers=_int_value(_dict_value(repository, "watchers"), "totalCount"),
        star_velocity=format_star_velocity(
            _int_value(repository, "stargazerCount"),
            [
                str(edge.get("starredAt"))
                for edge in _list_value(
                    _dict_value(repository, "recent_stars"), "edges"
                )
            ],
        ),
        topics=[
            str(node.get("topic", {}).get("name"))
            for node in _list_value(
                _dict_value(repository, "repositoryTopics"), "nodes"
            )
        ],
        features=_gql_features(repository),
        languages=_gql_languages(repository),
        tree_entries=tree_entries,
        docs_dir_name=docs_dir,
        docs_files=docs_files,
        ai_rules_listing=ai_listing,
        ai_rules_inline=ai_inline,
        dep_configs=_extract_gql_dep_configs(repository),
        readme=_extract_gql_readme(repository),
        context_files=context,
        too_large_context=too_large,
        extra_detected=_gql_extra_detected(repository),
        commits=_gql_commits(repository),
        monthly_commits=_gql_monthly_commits(repository),
        issues=[
            _gql_issue(issue)
            for issue in _list_value(_dict_value(repository, "issues"), "nodes")
        ],
        pull_requests=[
            _gql_pr(pr)
            for pr in _list_value(
                _dict_value(repository, "pullRequests"), "nodes"
            )
        ],
        releases=[
            _gql_release(release)
            for release in _list_value(
                _dict_value(repository, "releases"), "nodes"
            )
        ],
        api_source="graphql",
        rate_limit_remaining=_int_value(rate, "remaining"),
    )


def _rest_data(
    repo_data: Any,
    readme_raw: str | None,
    languages: Any,
    commits: Any,
    issues: Any,
    pulls: Any,
    releases: Any,
    stars: Any,
    tree_entries: list[TreeEntry],
    tree_paths: set[str],
    docs_dir: str | None,
    docs_files: list[str],
    context_files: dict[str, TextFile],
    ai_listing: dict[str, list[TextFile]],
    ai_inline: dict[str, TextFile],
    dep_configs: list[tuple[str, str]],
) -> RepoOverviewData:
    repo_mapping = _dict_value(repo_data)
    owner_data = _dict_value(repo_data, "owner")
    license_data = _dict_value(repo_data, "license")
    real_issues = [
        issue for issue in _coerce_list(issues) if "pull_request" not in issue
    ]
    return RepoOverviewData(
        full_name=str(repo_mapping.get("full_name") or ""),
        description=str(repo_mapping.get("description") or "_No description_"),
        owner=RepoOwner(
            str(owner_data.get("login", "")),
            str(owner_data.get("html_url", "")),
            str(owner_data.get("type", "")),
        ),
        license=RepoLicense(
            str(license_data.get("name", "")),
            str(license_data.get("spdx_id") or "NOASSERTION"),
        )
        if license_data
        else None,
        visibility=str(repo_mapping.get("visibility") or ""),
        default_branch=str(repo_mapping.get("default_branch") or "main"),
        created_at=str(repo_mapping.get("created_at") or ""),
        pushed_at=str(repo_mapping.get("pushed_at") or ""),
        is_fork=bool(repo_mapping.get("fork")),
        is_archived=bool(repo_mapping.get("archived")),
        fork_parent=_rest_parent(repo_data),
        disk_usage_bytes=_int_value(repo_data, "size") * 1024,
        stars=_int_value(repo_data, "stargazers_count"),
        forks=_int_value(repo_data, "forks_count"),
        open_issues_count=_int_value(repo_data, "open_issues_count"),
        open_prs_count=len(_coerce_list(pulls)),
        watchers=_int_value(repo_data, "subscribers_count")
        or _int_value(repo_data, "watchers_count"),
        star_velocity=format_star_velocity(
            _int_value(repo_data, "stargazers_count"),
            [str(star.get("starred_at")) for star in _coerce_list(stars)],
        ),
        topics=[
            str(topic)
            for topic in repo_mapping.get("topics", [])
            if isinstance(repo_mapping.get("topics"), list)
        ],
        features=", ".join(
            feature
            for feature in (
                "issues",
                "wiki",
                "discussions",
                "projects",
                "pages",
            )
            if repo_mapping.get(f"has_{feature}")
        ),
        languages={
            str(key): int(value)
            for key, value in _dict_value(languages).items()
        },
        tree_entries=tree_entries,
        docs_dir_name=docs_dir,
        docs_files=docs_files,
        ai_rules_listing=ai_listing,
        ai_rules_inline=ai_inline,
        dep_configs=dep_configs,
        readme=TextFile(readme_raw, len(readme_raw)) if readme_raw else None,
        context_files=context_files,
        extra_detected=[
            name
            for name in ("CONTRIBUTING.md", "CHANGELOG.md")
            if name in tree_paths and name not in context_files
        ],
        commits=[_rest_commit(commit) for commit in _coerce_list(commits)],
        issues=[_rest_issue(issue) for issue in real_issues],
        pull_requests=[_rest_pr(pr) for pr in _coerce_list(pulls)],
        releases=[_rest_release(release) for release in _coerce_list(releases)],
        api_source="rest",
    )


async def _fetch_rest_context_and_deps(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    tree_paths: set[str],
    timeout_s: float,
) -> tuple[dict[str, TextFile], list[tuple[str, str]]]:
    context_names = [name for name in CONTEXT_FILE_NAMES if name in tree_paths]
    dep_names = [name for name in DEP_CONFIG_ALLOWLIST if name in tree_paths]
    context_raws, dep_raws = await asyncio.gather(
        _fetch_rest_raw_files(
            client, token, base_url, owner, repo, context_names, timeout_s
        ),
        _fetch_rest_raw_files(
            client, token, base_url, owner, repo, dep_names, timeout_s
        ),
    )
    context = {
        name: TextFile(raw, len(raw))
        for name, raw in zip(context_names, context_raws, strict=True)
        if raw and len(raw) <= CONTEXT_FILE_LIMITS[name]
    }
    deps = [
        (name, raw)
        for name, raw in zip(dep_names, dep_raws, strict=True)
        if raw and len(raw) <= DEP_CONFIG_ALLOWLIST[name][1]
    ]
    return context, deps


async def _fetch_rest_raw_files(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    names: list[str],
    timeout_s: float,
) -> tuple[str | None, ...]:
    return tuple(
        await asyncio.gather(
            *(
                github_get_raw_safe(
                    client,
                    token,
                    base_url,
                    f"/repos/{owner}/{repo}/contents/{name}",
                    timeout_s,
                )
                for name in names
            )
        )
    )


async def _fetch_rest_ai_rules(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    full_tree: list[TreeEntry],
    timeout_s: float,
) -> tuple[dict[str, list[TextFile]], dict[str, TextFile]]:
    listing = {
        directory_path: [
            TextFile(entry.path.rsplit("/", maxsplit=1)[-1], entry.size or 0)
            for entry in full_tree
            if entry.type == "blob"
            and entry.path.startswith(f"{directory_path}/")
            and entry.path.count("/") == directory_path.count("/") + 1
        ]
        for directory_path in AI_RULES_DIRS
    }
    clean_listing = {path: files for path, files in listing.items() if files}
    inline = await _fetch_inline_ai_rules(
        client,
        token,
        base_url,
        owner,
        repo,
        clean_listing,
        timeout_s,
    )
    return clean_listing, inline


async def _fetch_inline_ai_rules(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    listing: dict[str, list[TextFile]],
    timeout_s: float,
) -> dict[str, TextFile]:
    inline_targets = [
        (directory_path, f"{directory_path}/{files[0].text}")
        for directory_path, files in listing.items()
        if len(files) == 1 and files[0].size <= AI_RULES_INLINE_MAX_BYTES
    ]
    raws = await asyncio.gather(
        *(
            github_get_raw_safe(
                client,
                token,
                base_url,
                f"/repos/{owner}/{repo}/contents/{path}",
                timeout_s,
            )
            for _, path in inline_targets
        )
    )
    return {
        directory_path: TextFile(raw, len(raw))
        for (directory_path, _), raw in zip(inline_targets, raws, strict=True)
        if raw
    }


def _process_rest_tree(
    tree_data: Any,
) -> tuple[list[TreeEntry], set[str], str | None, list[str]]:
    full_tree = [
        TreeEntry(
            str(entry.get("path")),
            str(entry.get("type")),
            _optional_int(entry.get("size")),
        )
        for entry in _list_value(tree_data, "tree")
    ]
    tree_paths = {entry.path for entry in full_tree}
    raw_tree = [entry for entry in full_tree if entry.path.count("/") <= 1]
    docs_dir = next(
        (
            entry.path
            for entry in raw_tree
            if entry.type == "tree" and entry.path.lower() in DOCS_DIR_NAMES
        ),
        None,
    )
    docs_files = [
        entry.path[len(docs_dir) + 1 :]
        for entry in full_tree
        if docs_dir
        and entry.type == "blob"
        and entry.path.startswith(f"{docs_dir}/")
        and is_docs_md_file(entry.path)
    ]
    return full_tree, tree_paths, docs_dir, docs_files


def _extract_gql_context_files(
    repository: dict[str, Any],
) -> tuple[dict[str, TextFile], list[str]]:
    context: dict[str, TextFile] = {}
    too_large: list[str] = []
    for name, alias in _context_alias_map().items():
        blob = extract_gql_blob(
            repository.get(alias), CONTEXT_FILE_LIMITS[name]
        )
        if blob:
            context[name] = blob
        elif _int_value(_dict_value(repository, alias), "byteSize"):
            too_large.append(
                f"{name} ({format_size(_int_value(_dict_value(repository, alias), 'byteSize'))} - too large to inline)"
            )
    return context, too_large


def _extract_gql_ai_rules(
    repository: dict[str, Any],
) -> tuple[dict[str, list[TextFile]], dict[str, TextFile]]:
    listing: dict[str, list[TextFile]] = {}
    inline: dict[str, TextFile] = {}
    for directory_path, (alias, _) in AI_RULES_DIRS.items():
        files = [
            TextFile(
                str(entry.get("name")),
                _int_value(_dict_value(entry, "object"), "byteSize"),
            )
            for entry in _list_value(_dict_value(repository, alias), "entries")
            if entry.get("type") == "blob"
        ]
        if files:
            listing[directory_path] = files
        if len(files) == 1 and files[0].size <= AI_RULES_INLINE_MAX_BYTES:
            text = str(
                _dict_value(
                    _list_value(_dict_value(repository, alias), "entries")[0],
                    "object",
                ).get("text")
                or ""
            )
            if text:
                inline[directory_path] = TextFile(files[0].text, len(text))
    return listing, inline


def _extract_gql_dep_configs(
    repository: dict[str, Any],
) -> list[tuple[str, str]]:
    return [
        (name, blob.text)
        for name, (alias, max_bytes) in DEP_CONFIG_ALLOWLIST.items()
        if (blob := extract_gql_blob(repository.get(alias), max_bytes))
    ]


def _extract_gql_readme(repository: dict[str, Any]) -> TextFile | None:
    return next(
        (
            blob
            for index in range(5)
            if (
                blob := extract_gql_blob(
                    repository.get(f"readme_{index}"), README_MAX_BYTES
                )
            )
        ),
        None,
    )


def _default_branch_from_gql(repository: dict[str, Any]) -> str:
    branch_ref = _dict_value(repository, "defaultBranchRef")
    return str(branch_ref.get("name") or "main")


def _gql_owner(repository: dict[str, Any]) -> RepoOwner:
    owner = _dict_value(repository, "owner")
    return RepoOwner(
        str(owner.get("login", "")),
        str(owner.get("url", "")),
        str(owner.get("__typename", "")),
    )


def _gql_license(repository: dict[str, Any]) -> RepoLicense | None:
    license_info = _dict_value(repository, "licenseInfo")
    return (
        RepoLicense(
            str(license_info.get("name", "")),
            str(license_info.get("spdxId", "")),
        )
        if license_info
        else None
    )


def _gql_parent(repository: dict[str, Any]) -> ForkParent | None:
    parent = _dict_value(repository, "parent")
    return (
        ForkParent(
            str(parent.get("nameWithOwner", "")), str(parent.get("url", ""))
        )
        if parent
        else None
    )


def _gql_features(repository: dict[str, Any]) -> str:
    return ", ".join(
        feature.lower()
        for feature in ("Issues", "Wiki", "Discussions", "Projects")
        if repository.get(f"has{feature}Enabled")
    )


def _gql_languages(repository: dict[str, Any]) -> dict[str, int]:
    return {
        str(_dict_value(edge, "node").get("name")): _int_value(edge, "size")
        for edge in _list_value(_dict_value(repository, "languages"), "edges")
    }


def _gql_commits(repository: dict[str, Any]) -> list[RepoCommit]:
    target = _dict_value(_dict_value(repository, "defaultBranchRef"), "target")
    history = _dict_value(target, "history")
    return [
        RepoCommit(
            str(commit.get("committedDate", "")),
            str(_dict_value(commit, "author").get("name", "unknown")),
            str(commit.get("message", "")).split("\n", maxsplit=1)[0][:80],
        )
        for commit in _list_value(history, "nodes")
    ]


def _gql_monthly_commits(repository: dict[str, Any]) -> list[tuple[str, int]]:
    target = _dict_value(_dict_value(repository, "defaultBranchRef"), "target")
    monthly = [
        (
            key.removeprefix("m").replace("_", "-"),
            _int_value(value, "totalCount"),
        )
        for key, value in target.items()
        if key.startswith("m")
        and isinstance(value, dict)
        and _int_value(value, "totalCount") > 0
    ]
    return sorted(monthly)


def _gql_issue(issue: dict[str, Any]) -> RepoIssue:
    return RepoIssue(
        _int_value(issue, "number"),
        str(issue.get("title", "")),
        str(issue.get("state", "")).lower(),
        str(_dict_value(issue, "author").get("login", "ghost")),
        _label_text(_list_value(_dict_value(issue, "labels"), "nodes")),
        str(issue.get("updatedAt", "")),
        str(issue.get("body", "") or ""),
    )


def _gql_pr(pull_request: dict[str, Any]) -> RepoPullRequest:
    return RepoPullRequest(
        _int_value(pull_request, "number"),
        str(pull_request.get("title", "")),
        str(_dict_value(pull_request, "author").get("login", "ghost")),
        _label_text(_list_value(_dict_value(pull_request, "labels"), "nodes")),
        str(pull_request.get("updatedAt", "")),
        bool(pull_request.get("isDraft")),
        str(pull_request.get("body", "") or ""),
    )


def _gql_release(release: dict[str, Any]) -> RepoRelease:
    tag = str(release.get("tagName", ""))
    return RepoRelease(
        str(release.get("name") or tag),
        tag,
        str(release.get("publishedAt", "")),
        bool(release.get("isPrerelease")),
        str(release.get("description", "") or ""),
    )


def _rest_parent(repo_data: Any) -> ForkParent | None:
    parent = _dict_value(repo_data, "parent")
    return (
        ForkParent(
            str(parent.get("full_name", "")), str(parent.get("html_url", ""))
        )
        if parent
        else None
    )


def _rest_commit(commit: dict[str, Any]) -> RepoCommit:
    commit_obj = _dict_value(commit, "commit")
    author = _dict_value(commit_obj, "author")
    return RepoCommit(
        str(author.get("date", "")),
        str(author.get("name", "unknown")),
        str(commit_obj.get("message", "")).split("\n", maxsplit=1)[0][:80],
    )


def _rest_issue(issue: dict[str, Any]) -> RepoIssue:
    return RepoIssue(
        _int_value(issue, "number"),
        str(issue.get("title", "")),
        str(issue.get("state", "")),
        str(_dict_value(issue, "user").get("login", "ghost")),
        _label_text(_coerce_list(issue.get("labels"))),
        str(issue.get("updated_at", "")),
        str(issue.get("body", "") or ""),
    )


def _rest_pr(pull_request: dict[str, Any]) -> RepoPullRequest:
    return RepoPullRequest(
        _int_value(pull_request, "number"),
        str(pull_request.get("title", "")),
        str(_dict_value(pull_request, "user").get("login", "ghost")),
        _label_text(_coerce_list(pull_request.get("labels"))),
        str(pull_request.get("updated_at", "")),
        bool(pull_request.get("draft")),
        str(pull_request.get("body", "") or ""),
    )


def _rest_release(release: dict[str, Any]) -> RepoRelease:
    tag = str(release.get("tag_name", ""))
    return RepoRelease(
        str(release.get("name") or tag),
        tag,
        str(release.get("published_at", "")),
        bool(release.get("prerelease")),
        str(release.get("body", "") or ""),
    )


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


def _gql_extra_detected(repository: dict[str, Any]) -> list[str]:
    return [
        name
        for name, alias in (
            ("CONTRIBUTING.md", "contributing_md"),
            ("CHANGELOG.md", "changelog_md"),
        )
        if repository.get(alias)
    ]


def _label_text(labels: list[dict[str, Any]]) -> str:
    return " ".join(f"`{label.get('name', '')}`" for label in labels)


def _list_value(data: Any, key: str) -> list[dict[str, Any]]:
    value = _dict_value(data).get(key, [])
    return _coerce_list(value)


def _dict_value(data: Any, key: str | None = None) -> dict[str, Any]:
    value = (
        data.get(key, {})
        if key is not None and isinstance(data, dict)
        else data
    )
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[dict[str, Any]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _int_value(data: Any, key: str) -> int:
    value = _dict_value(data).get(key) if isinstance(data, dict) else None
    return value if isinstance(value, int) else 0


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None
