"""Repository overview markdown renderer."""

# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime, UTC

from omnifetch.fetch.providers.github.formatters import (
    escape_table_cell,
    format_ai_rules_listing,
    format_commit_activity,
    format_date,
    format_dep_configs,
    format_depth2_tree,
    format_docs_listing,
    format_language_breakdown,
    format_size,
    snippet_two_sentences,
    truncate_readme,
)
from omnifetch.fetch.providers.github.types import RepoOverviewData
from omnifetch.fetch.shared.types import FetchResult

_CONTEXT_RENDER_ORDER = (
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    "AGENT.md",
    "ARCHITECTURE.md",
    "DEVELOPMENT.md",
    "CONVENTIONS.md",
    "REVIEW.md",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".goosehints",
    ".roorules",
    ".continuerules",
    ".github/copilot-instructions.md",
    ".junie/guidelines.md",
)


def build_repo_overview_result(data: RepoOverviewData) -> FetchResult:
    """Build a normalized fetch result for a repository overview."""
    content = (
        _render_identity(data)
        + _render_stats(data)
        + _render_structure(data)
        + _render_context_files(data)
        + _render_activity(data)
        + _render_issues(data)
        + _render_pull_requests(data)
        + _render_releases(data)
        + _render_llms_full(data)
        + _render_ai_summary(data)
        + _render_footer(data)
    )
    truncation = truncate_readme(content)
    return FetchResult(
        url=f"https://github.com/{data.full_name}",
        title=f"{data.full_name} - {data.description}",
        content=truncation.content,
        source_provider="github",
        metadata=_metadata(
            data, truncation.readme_truncated, truncation.readme_original_tokens
        ),
    )


def _render_identity(data: RepoOverviewData) -> str:
    lines = [
        f"# {data.full_name}\n\n> {data.description}\n",
        "\n**Project Identity**",
        "| Field | Value |\n|-------|-------|",
        f"| Owner | [{data.owner.login}]({data.owner.url}) ({data.owner.type}) |",
        f"| License | {_license_text(data)} |",
        f"| Visibility | {data.visibility} |",
        f"| Default Branch | `{data.default_branch}` |",
        f"| Created | {format_date(data.created_at)} |",
        f"| Last Push | {format_date(data.pushed_at)} |",
    ]
    if data.is_fork and data.fork_parent:
        lines.append(
            f"| Forked From | [{data.fork_parent.name}]({data.fork_parent.url}) |"
        )
    lines.append(f"| Archived | {'Yes' if data.is_archived else 'No'} |")
    lines.append("")
    return "\n".join(lines)


def _render_stats(data: RepoOverviewData) -> str:
    result = (
        f"\n**Stats:** {data.stars} stars, {data.forks} forks, "
        f"{data.open_issues_count} open issues, {data.open_prs_count} open PRs, "
        f"{data.watchers} watchers, {format_size(data.disk_usage_bytes)}\n"
    )
    if data.star_velocity:
        result += f"**Star velocity:** {data.star_velocity}\n"
    result += "\n"
    if data.topics:
        result += (
            f"**Topics:** {' '.join(f'`{topic}`' for topic in data.topics)}\n\n"
        )
    if data.features:
        result += f"**Features:** {data.features}\n\n"
    return result


def _render_structure(data: RepoOverviewData) -> str:
    result = ""
    if data.languages:
        result += "## Languages\n\n| Language | Share | Size |\n|----------|-------|------|\n"
        result += format_language_breakdown(data.languages) + "\n\n"
    if data.tree_entries:
        result += "## Directory Structure (depth 2)\n\n```\n"
        result += format_depth2_tree(data.tree_entries) + "\n```\n\n"
    if data.docs_dir_name:
        result += format_docs_listing(data.docs_dir_name, data.docs_files)
    result += format_ai_rules_listing(data.ai_rules_listing)
    return result + format_dep_configs(data.dep_configs)


def _render_context_files(data: RepoOverviewData) -> str:
    result = ""
    llms = data.context_files.get("llms.txt")
    if llms:
        result += (
            f"## llms.txt\n\n`````markdown\n{llms.text.rstrip()}\n`````\n\n"
        )
    if data.readme:
        result += f"## README\n\n{data.readme.text}\n\n"
    for name in _CONTEXT_RENDER_ORDER:
        context_file = data.context_files.get(name)
        if context_file:
            result += f"## {name}\n\n`````markdown\n{context_file.text.rstrip()}\n`````\n\n"
    for directory_path, file in data.ai_rules_inline.items():
        result += (
            f"## {directory_path}\n\n`````\n{file.text.rstrip()}\n`````\n\n"
        )
    return result


def _render_activity(data: RepoOverviewData) -> str:
    result = ""
    if data.commits:
        rows = "\n".join(
            f"| {format_date(commit.date)} | {escape_table_cell(commit.author)} | "
            f"{escape_table_cell(commit.message)} |"
            for commit in data.commits
        )
        result += "## Recent Commits\n\n| Date | Author | Message |\n|------|--------|---------|\n"
        result += rows + "\n\n"
    return result + format_commit_activity(data.monthly_commits)


def _render_issues(data: RepoOverviewData) -> str:
    if not data.issues:
        return ""
    rows = [
        f"### #{issue.number}: {issue.title}\n"
        f"**State:** {issue.state} | **Labels:** {issue.labels or 'none'} | "
        f"**Author:** @{issue.author} | **Updated:** {format_date(issue.updated_at)}\n\n"
        f"{_truncated_body(issue.body, 500)}"
        for issue in data.issues
    ]
    return "## Open Issues\n\n" + "\n\n".join(rows) + "\n\n"


def _render_pull_requests(data: RepoOverviewData) -> str:
    if not data.pull_requests:
        return ""
    rows = [
        f"### #{pull_request.number}: {pull_request.title}"
        f"{' (draft)' if pull_request.is_draft else ''}\n"
        f"**Author:** @{pull_request.author} | "
        f"**Labels:** {pull_request.labels or 'none'} | "
        f"**Updated:** {format_date(pull_request.updated_at)}\n"
        f"{snippet_two_sentences(pull_request.body)}\n"
        for pull_request in data.pull_requests
    ]
    return "## Open Pull Requests\n\n" + "\n".join(rows) + "\n"


def _render_releases(data: RepoOverviewData) -> str:
    if not data.releases:
        return ""
    rows = [
        f"### {release.name or release.tag} (`{release.tag}`)\n"
        f"**Published:** {format_date(release.published_at)}"
        f"{' | **Pre-release**' if release.is_prerelease else ''}\n\n"
        f"{_truncated_body(release.body, 1000)}"
        for release in data.releases
    ]
    return "## Recent Releases\n\n" + "\n\n".join(rows) + "\n\n"


def _render_llms_full(data: RepoOverviewData) -> str:
    llms_full = data.context_files.get("llms-full.txt")
    if llms_full is None:
        return ""
    return f"## llms-full.txt\n\n`````markdown\n{llms_full.text.rstrip()}\n`````\n\n"


def _render_ai_summary(data: RepoOverviewData) -> str:
    inlined = list(data.context_files)
    ai_notes = [
        f"{directory_path}/ (inlined above)"
        for directory_path in data.ai_rules_inline
    ]
    ai_notes.extend(
        f"{directory_path}/ ({len(files)} files listed above)"
        for directory_path, files in data.ai_rules_listing.items()
        if directory_path not in data.ai_rules_inline
    )
    all_entries = [
        *inlined,
        *ai_notes,
        *data.too_large_context,
        *data.extra_detected,
    ]
    if not all_entries:
        return ""
    rows = "".join(f"- `{entry}` (inlined above)\n" for entry in inlined)
    rows += "".join(f"- `{entry}`\n" for entry in ai_notes)
    rows += "".join(f"- `{entry}`\n" for entry in data.too_large_context)
    rows += "".join(
        f"- `{entry}` (detected)\n" for entry in data.extra_detected
    )
    return f"## AI Context Files\n\n{rows}\n"


def _render_footer(data: RepoOverviewData) -> str:
    api_label = (
        "GitHub GraphQL API"
        if data.api_source == "graphql"
        else "GitHub REST API"
    )
    rate_info = (
        f" | Rate limit: {data.rate_limit_remaining} remaining"
        if data.rate_limit_remaining is not None
        else ""
    )
    return f"---\n*Fetched via {api_label} at {datetime.now(tz=UTC).isoformat()}{rate_info}*\n"


def _metadata(
    data: RepoOverviewData,
    readme_truncated: bool,
    readme_original_tokens: int | None,
) -> dict[str, object]:
    ai_context_files = [
        *data.context_files,
        *(f"{path}/" for path in data.ai_rules_inline),
        *(
            f"{path}/"
            for path in data.ai_rules_listing
            if path not in data.ai_rules_inline
        ),
        *data.extra_detected,
    ]
    metadata: dict[str, object] = {
        "resource_type": "repo_overview",
        "stars": data.stars,
        "forks": data.forks,
        "language": next(iter(data.languages), None),
        "archived": data.is_archived,
        "default_branch": data.default_branch,
        "ai_context_files": ai_context_files,
        "graphql": data.api_source == "graphql",
        "readme_truncated": readme_truncated,
    }
    if readme_original_tokens is not None:
        metadata["readme_original_tokens"] = readme_original_tokens
    return metadata


def _license_text(data: RepoOverviewData) -> str:
    if data.license is None:
        return "None"
    return f"{data.license.name} ({data.license.id})"


def _truncated_body(text: str, max_chars: int) -> str:
    if not text:
        return ""
    suffix = "..." if len(text) > max_chars else ""
    return f"{text[:max_chars]}{suffix}\n"
