"""Tests for the GitHub fetch provider."""

from __future__ import annotations

from datetime import datetime, timedelta, UTC
from typing import Any, cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.github.formatters as github_formatters
import omnifetch.fetch.providers.github.handlers as github_handlers
import omnifetch.fetch.providers.github.handlers_file as github_handlers_file
import omnifetch.fetch.providers.github.provider as github_provider_dispatch
import omnifetch.fetch.providers.github.repo_overview as github_repo_overview
import omnifetch.fetch.providers.github.url_parser as github_url_parser
from omnifetch.fetch.providers import get_active_fetch_providers
from omnifetch.fetch.providers.base import get_provider_classes
from omnifetch.fetch.providers.github import (
    graphql,
)
from omnifetch.fetch.providers.github import (
    markdown_builder as github_markdown_builder,
)
from omnifetch.fetch.providers.github.api import (
    api_headers,
    github_get,
    github_get_raw_safe,
    github_get_safe,
    github_get_starred,
    raw_headers,
)
from omnifetch.fetch.providers.github.constants import API_BASE_URL
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
    format_star_velocity,
    is_binary,
    is_docs_md_file,
    snippet_two_sentences,
    truncate_readme,
)
from omnifetch.fetch.providers.github.handlers import (
    fetch_actions,
    fetch_commit,
    fetch_commit_list,
    fetch_gist,
    fetch_issue,
    fetch_issue_list,
    fetch_pr_list,
    fetch_pull_request,
    fetch_release,
    fetch_release_latest,
    fetch_release_list,
    fetch_user_profile,
)
from omnifetch.fetch.providers.github.handlers_file import (
    fetch_directory,
    fetch_file,
    fetch_raw_file,
    fetch_wiki_page,
)
from omnifetch.fetch.providers.github.markdown_builder import (
    build_repo_overview_result,
)
from omnifetch.fetch.providers.github.provider import GitHubFetchProvider
from omnifetch.fetch.providers.github.repo_overview import (
    fetch_repo_overview,
    fetch_repo_overview_gql,
    fetch_repo_overview_rest,
)
from omnifetch.fetch.providers.github.types import (
    ForkParent,
    ParsedGitHubUrl,
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
from omnifetch.fetch.providers.github.url_parser import parse_github_url
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_TOKEN = "github-secret"
_OWNER = "octo"
_REPO = "repo"


def _secrets() -> ProviderSecrets:
    return ProviderSecrets({"GITHUB_API_KEY": _TOKEN})


def _request_json(request: httpx.Request) -> dict[str, object]:
    payload = request.read()
    return cast(dict[str, object], httpx.Response(200, content=payload).json())


def _simple_result(title: str) -> FetchResult:
    return FetchResult(
        url="https://github.com/o/r",
        title=title,
        content=f"# {title}",
        source_provider="github",
    )


def _parsed_github_url(url: str) -> ParsedGitHubUrl:
    parsed = parse_github_url(url)
    assert parsed is not None
    return parsed


def _result_metadata(result: FetchResult) -> dict[str, Any]:
    assert result.metadata is not None
    return result.metadata


@pytest.mark.parametrize(
    ("url", "resource_type", "owner", "repo", "ref", "path", "resource_id"),
    [
        (
            "https://github.com/octo",
            "user_profile",
            "octo",
            None,
            None,
            None,
            None,
        ),
        (
            "https://github.com/orgs/octo",
            "org_profile",
            "octo",
            None,
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo",
            "repo_overview",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/blob/main/src/app.py",
            "file",
            "octo",
            "repo",
            "main/src/app.py",
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/blob/"
            "0123456789abcdef0123456789abcdef01234567/src/app.py",
            "file",
            "octo",
            "repo",
            "0123456789abcdef0123456789abcdef01234567",
            "src/app.py",
            None,
        ),
        (
            "https://github.com/octo/repo/tree/main/docs",
            "directory",
            "octo",
            "repo",
            "main/docs",
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/issues",
            "issue_list",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/issues/7",
            "issue",
            "octo",
            "repo",
            None,
            None,
            "7",
        ),
        (
            "https://github.com/octo/repo/pulls",
            "pr_list",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/pull/8",
            "pull_request",
            "octo",
            "repo",
            None,
            None,
            "8",
        ),
        (
            "https://github.com/octo/repo/pull/8/files",
            "pr_files",
            "octo",
            "repo",
            None,
            None,
            "8",
        ),
        (
            "https://github.com/octo/repo/pull/8.diff",
            "pull_request",
            "octo",
            "repo",
            None,
            None,
            "8",
        ),
        (
            "https://github.com/octo/repo/wiki/Page-One",
            "wiki_page",
            "octo",
            "repo",
            None,
            None,
            "Page-One",
        ),
        (
            "https://github.com/octo/repo/wiki",
            "wiki",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/releases",
            "release_list",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/releases/latest",
            "release_latest",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/releases/tag/v1.0",
            "release",
            "octo",
            "repo",
            None,
            None,
            "v1.0",
        ),
        (
            "https://github.com/octo/repo/commits/main",
            "commit_list",
            "octo",
            "repo",
            "main",
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/commit/abc.patch",
            "commit",
            "octo",
            "repo",
            None,
            None,
            "abc",
        ),
        (
            "https://github.com/octo/repo/actions",
            "actions",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://github.com/octo/repo/actions/runs/9",
            "action_run",
            "octo",
            "repo",
            None,
            None,
            "9",
        ),
        (
            "https://github.com/octo/repo/compare/a...b",
            "compare",
            "octo",
            "repo",
            None,
            None,
            "a...b",
        ),
        (
            "https://github.com/octo/repo/discussions/3",
            "discussion",
            "octo",
            "repo",
            None,
            None,
            "3",
        ),
        (
            "https://github.com/octo/repo/discussions",
            "discussion_list",
            "octo",
            "repo",
            None,
            None,
            None,
        ),
        (
            "https://gist.github.com/octo/abc123",
            "gist",
            "octo",
            None,
            None,
            None,
            "abc123",
        ),
        (
            "https://gist.github.com/abc123",
            "gist",
            None,
            None,
            None,
            None,
            "abc123",
        ),
        (
            "https://raw.githubusercontent.com/octo/repo/main/README.md",
            "raw_file",
            "octo",
            "repo",
            "main",
            "README.md",
            None,
        ),
    ],
)
def test_github_url_parser_parity(
    url: str,
    resource_type: str,
    owner: str | None,
    repo: str | None,
    ref: str | None,
    path: str | None,
    resource_id: str | None,
) -> None:
    parsed = _parsed_github_url(url)
    assert parsed.resource_type == resource_type
    assert parsed.owner == owner
    assert parsed.repo == repo
    assert parsed.ref == ref
    assert parsed.path == path
    assert parsed.resource_id == resource_id


@pytest.mark.parametrize(
    "url",
    [
        "notaurl",
        "https://example.com/octo/repo",
        "https://github.com/search",
        "https://gist.github.com/not-a-hex",
        "https://raw.githubusercontent.com/octo",
    ],
)
def test_github_url_parser_rejects_unknown_urls(url: str) -> None:
    assert parse_github_url(url) is None


def test_github_url_parser_edge_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert parse_github_url("http://[") is None
    assert parse_github_url("https://github.com/") is None
    assert parse_github_url("https://gist.github.com/") is None
    assert parse_github_url("https://github.com/octo/repo/raw/main") is not None
    assert (
        _parsed_github_url(
            "https://github.com/octo/repo/pull/not-a-number"
        ).resource_type
        == "repo_overview"
    )
    assert (
        _parsed_github_url(
            "https://github.com/octo/repo/releases/expanded"
        ).resource_type
        == "repo_overview"
    )
    assert (
        _parsed_github_url("https://github.com/octo/repo/commits").resource_type
        == "commit_list"
    )
    assert (
        _parsed_github_url("https://github.com/octo/repo/blob").resource_type
        == "file"
    )
    assert (
        _parsed_github_url("https://github.com/octo/repo/pulse").resource_type
        == "repo_overview"
    )

    def raise_value_error(segment: str) -> str:
        raise ValueError(segment)

    monkeypatch.setattr(github_url_parser, "unquote", raise_value_error)
    assert (
        _parsed_github_url("https://github.com/octo/repo").resource_type
        == "repo_overview"
    )


def test_github_formatters_cover_edges() -> None:
    assert escape_table_cell("a|b\nc\r") == r"a\|b c"
    assert format_size(5) == "5 B"
    assert format_size(2048) == "2.0 KB"
    assert format_size(2 * 1024 * 1024) == "2.0 MB"
    assert format_date(None) == "N/A"
    assert format_date("2026-06-29T12:00:00Z") == "2026-06-29"
    assert format_language_breakdown({}) == "_No languages detected_"
    assert "| Python | 75.0% | 3.0 KB |" in format_language_breakdown(
        {"Python": 3072, "Rust": 1024}
    )
    assert is_binary("image.png")
    assert not is_binary("README.md")
    assert is_docs_md_file("guide.mdx")
    assert not is_docs_md_file("guide-ES.md")
    assert snippet_two_sentences(
        "<!--x-->First sentence here. Second one here. Third."
    ) == ("First sentence here. Second one here.")
    assert snippet_two_sentences(None) == ""


def test_github_star_velocity_and_truncation() -> None:
    now = datetime.now(tz=UTC)
    recent = [
        (now - timedelta(minutes=minute)).isoformat() for minute in range(3)
    ]
    assert "/day" in format_star_velocity(10, recent)
    assert format_star_velocity(0, recent) == ""
    assert format_star_velocity(10, recent[:1]) == ""
    content = "Before\n\n## README\n\n" + ("# A\n" + "x" * 25_000)
    content += "\n## AGENTS.md\n\nagent notes"
    truncated = truncate_readme(content)
    assert truncated.readme_truncated is True
    assert "README truncated" in truncated.content
    assert "Table of Contents" in truncated.content
    assert truncate_readme("no readme").readme_truncated is False
    assert truncate_readme("## README\n\nshort").content == "## README\n\nshort"
    no_heading = "## README\n\n" + ("plain text\n" * 2500)
    assert "Table of Contents" not in truncate_readme(no_heading).content


def test_github_private_formatter_branches() -> None:
    assert (
        github_formatters._format_rate_per_day(150) == "~150/day (~4500/month)"
    )
    assert github_formatters._format_rate_per_day(15) == "~15/day (~105/week)"
    assert github_formatters._format_rate_per_day(2.5) == "~2.5/day (~18/week)"
    assert github_formatters._format_rate_per_day(0.2) == "~1.4/week (~6/month)"
    assert github_formatters._format_rate_per_day(0.01) == "~0.3/month"
    assert github_formatters._next_fence_marker("", "```python") == "```"
    assert github_formatters._next_fence_marker("```", "```") == ""
    assert github_formatters._next_fence_marker("```", "```x") == "```"


def test_github_listing_formatters() -> None:
    assert format_docs_listing("docs", ["a.md"]) == (
        "## Documentation Files\n\n`docs/` - 1 markdown files:\n\n"
        "```\na.md\n```\n\n"
    )
    assert format_docs_listing("docs", []) == ""
    assert "2 files" in format_ai_rules_listing(
        {".cursor/rules": [TextFile("a.md", 10), TextFile("b.md", 20)]}
    )
    assert format_ai_rules_listing({}) == ""
    assert "```json" in format_dep_configs([("package.json", "{}")])
    assert "```toml" in format_dep_configs([("pyproject.toml", "[x]")])
    assert "```yaml" in format_dep_configs([("pnpm-workspace.yaml", "x")])
    assert "### Gemfile" in format_dep_configs([("Gemfile", "source")])
    assert format_dep_configs([]) == ""
    assert "last 2 years" in format_commit_activity([("2026-01", 2)])
    assert format_commit_activity([]) == ""
    tree = [
        TreeEntry("src", "tree"),
        TreeEntry("src/main.py", "blob", 12),
        TreeEntry("src/pkg", "tree"),
        TreeEntry("README.md", "blob", 5),
    ]
    assert "src/" in format_depth2_tree(tree)
    assert "main.py (12 B)" in format_depth2_tree(tree)


def test_github_graphql_helpers() -> None:
    assert graphql.extract_gql_blob(None, 10) is None
    assert graphql.extract_gql_blob({"text": "x", "byteSize": "1"}, 10) is None
    assert graphql.extract_gql_blob({"text": "x", "byteSize": 20}, 10) is None
    assert graphql.extract_gql_blob(
        {"text": "x", "byteSize": 1}, 10
    ) == TextFile("x", 1)
    assert "RepoOverview" in graphql.build_core_gql()
    assert "TreeChildren" in graphql.build_tree_children_query(["src"])
    root: list[dict[str, Any]] = [
        {"name": "src", "type": "tree"},
        {"name": "README.md", "type": "blob", "object": {"byteSize": 9}},
        {"name": "tests", "type": "tree"},
    ]
    children: dict[str, Any] = {
        "d0": {
            "entries": [
                {"name": "app.py", "type": "blob", "object": {"byteSize": 4}},
                {"name": "pkg", "type": "tree"},
            ]
        }
    }
    merged = graphql.merge_tree_children(root, children, ["src"])
    assert TreeEntry("src/app.py", "blob", 4) in merged
    assert graphql.merge_tree_children(root, None)[0] == TreeEntry(
        "src", "tree"
    )
    assert TreeEntry("src/pkg", "tree") in merged
    bad_root: list[dict[str, Any]] = [
        {"name": "bad.bin", "type": "blob", "object": {"byteSize": "x"}}
    ]
    assert graphql.merge_tree_children(
        bad_root,
        None,
    ) == [TreeEntry("bad.bin", "blob", None)]
    assert graphql._byte_size(None) is None
    assert graphql.filter_rest_tree(
        [
            TreeEntry("src", "tree"),
            TreeEntry("src/app.py", "blob"),
            TreeEntry("tests/test.py", "blob"),
            TreeEntry("src/pkg/deep.py", "blob"),
        ]
    ) == [TreeEntry("src", "tree"), TreeEntry("src/app.py", "blob")]


async def test_github_api_helpers_headers_and_safe_paths() -> None:
    assert api_headers(_TOKEN)["Authorization"] == "Bearer github-secret"
    assert raw_headers(_TOKEN)["Accept"] == "application/vnd.github.raw+json"
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{API_BASE_URL}/ok").respond(json={"ok": True})
        router.get(f"{API_BASE_URL}/missing").respond(
            404, json={"message": "no"}
        )
        router.get(f"{API_BASE_URL}/raw").respond(404, json={"message": "no"})
        router.get(f"{API_BASE_URL}/stars").respond(404, json={"message": "no"})
        async with httpx.AsyncClient() as client:
            assert await github_get(
                client, _TOKEN, API_BASE_URL, "/ok", 1.0
            ) == {"ok": True}
            assert (
                await github_get_safe(
                    client, _TOKEN, API_BASE_URL, "/missing", 1.0
                )
                is None
            )
            assert (
                await github_get_raw_safe(
                    client, _TOKEN, API_BASE_URL, "/raw", 1.0
                )
                is None
            )
            assert (
                await github_get_starred(
                    client, _TOKEN, API_BASE_URL, "/stars", 1.0
                )
                is None
            )


async def test_github_provider_registers_and_dispatches() -> None:
    assert get_provider_classes()["github"] is GitHubFetchProvider
    assert get_active_fetch_providers(_secrets()) == ["github"]
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{API_BASE_URL}/users/octo").respond(json=_user_payload())
        router.get(f"{API_BASE_URL}/users/octo/repos").respond(json=[])
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(_secrets(), client)
            result = await unified.fetch_url(
                "https://github.com/octo", provider="github"
            )
    assert result.source_provider == "github"
    assert result.metadata == {
        "resource_type": "user_profile",
        "public_repos": 2,
        "followers": 3,
    }


async def test_github_provider_errors() -> None:
    async with httpx.AsyncClient() as client:
        provider = GitHubFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as missing:
            await provider.fetch_url("https://github.com/octo")
        assert missing.value.error_type is ErrorType.INVALID_INPUT
        provider = GitHubFetchProvider(_secrets(), client)
        with pytest.raises(ProviderError) as invalid:
            await provider.fetch_url("https://example.com")
        assert invalid.value.error_type is ErrorType.INVALID_INPUT
        with pytest.raises(ProviderError) as unsupported:
            await provider.fetch_url(
                "https://github.com/octo/repo/compare/a...b"
            )
        assert unsupported.value.error_type is ErrorType.INVALID_INPUT


@pytest.mark.parametrize(
    ("parsed", "handler_name"),
    [
        (
            ParsedGitHubUrl("repo_overview", owner="o", repo="r"),
            "fetch_repo_overview",
        ),
        (ParsedGitHubUrl("wiki", owner="o", repo="r"), "fetch_repo_overview"),
        (ParsedGitHubUrl("file", owner="o", repo="r"), "fetch_file"),
        (ParsedGitHubUrl("directory", owner="o", repo="r"), "fetch_directory"),
        (
            ParsedGitHubUrl("issue", owner="o", repo="r", resource_id="1"),
            "fetch_issue",
        ),
        (
            ParsedGitHubUrl("issue_list", owner="o", repo="r"),
            "fetch_issue_list",
        ),
        (ParsedGitHubUrl("pr_list", owner="o", repo="r"), "fetch_pr_list"),
        (
            ParsedGitHubUrl(
                "wiki_page", owner="o", repo="r", resource_id="Home"
            ),
            "fetch_wiki_page",
        ),
        (
            ParsedGitHubUrl(
                "pull_request", owner="o", repo="r", resource_id="1"
            ),
            "fetch_pull_request",
        ),
        (
            ParsedGitHubUrl("pr_files", owner="o", repo="r", resource_id="1"),
            "fetch_pull_request",
        ),
        (
            ParsedGitHubUrl("release_list", owner="o", repo="r"),
            "fetch_release_list",
        ),
        (
            ParsedGitHubUrl("release", owner="o", repo="r", resource_id="v1"),
            "fetch_release",
        ),
        (
            ParsedGitHubUrl("release_latest", owner="o", repo="r"),
            "fetch_release_latest",
        ),
        (
            ParsedGitHubUrl("commit_list", owner="o", repo="r", ref="main"),
            "fetch_commit_list",
        ),
        (
            ParsedGitHubUrl("commit", owner="o", repo="r", resource_id="abc"),
            "fetch_commit",
        ),
        (ParsedGitHubUrl("actions", owner="o", repo="r"), "fetch_actions"),
        (ParsedGitHubUrl("org_profile", owner="o"), "fetch_user_profile"),
        (ParsedGitHubUrl("gist", resource_id="abc123"), "fetch_gist"),
        (
            ParsedGitHubUrl("raw_file", owner="o", repo="r", ref="main"),
            "fetch_raw_file",
        ),
    ],
)
async def test_github_provider_dispatch_matrix(
    monkeypatch: pytest.MonkeyPatch,
    parsed: ParsedGitHubUrl,
    handler_name: str,
) -> None:
    calls: list[str] = []

    async def fake_handler(*args: object) -> object:
        calls.append(handler_name)
        return _simple_result(handler_name)

    monkeypatch.setattr(github_provider_dispatch, handler_name, fake_handler)
    async with httpx.AsyncClient() as client:
        provider = GitHubFetchProvider(_secrets(), client)
        result = await provider._dispatch(parsed)

    assert calls == [handler_name]
    assert result.title == handler_name


async def test_github_issue_handlers() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{API_BASE_URL}/repos/octo/repo/issues/1").respond(
            json=_issue_payload()
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/issues/1/comments").respond(
            json=[_comment_payload()]
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/issues").respond(
            json=[
                _issue_payload(),
                {**_issue_payload(), "number": 2, "pull_request": {}},
            ]
        )
        async with httpx.AsyncClient() as client:
            issue = await fetch_issue(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "1", 1.0
            )
            issues = await fetch_issue_list(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
    assert "## Comments" in issue.content
    assert issues.metadata == {"resource_type": "issue_list", "count": 1}


async def test_github_pr_handlers() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{API_BASE_URL}/repos/octo/repo/pulls").respond(
            json=[_pr_payload()]
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/pulls/5").respond(
            json=_pr_payload()
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/pulls/5/files").respond(
            json=[_file_payload()]
        )
        async with httpx.AsyncClient() as client:
            pr_list = await fetch_pr_list(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
            pr = await fetch_pull_request(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "5", True, 1.0
            )
    assert pr_list.metadata == {"resource_type": "pr_list", "count": 1}
    assert "Changed Files" in pr.content
    assert _result_metadata(pr)["resource_type"] == "pr_files"


async def test_github_release_commit_profile_gist_actions_handlers() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{API_BASE_URL}/repos/octo/repo/releases").respond(
            json=[_release_payload()]
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/releases/tags/v1").respond(
            json=_release_payload()
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/releases/latest").respond(
            json=_release_payload()
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/commits").respond(
            json=[_commit_list_payload()]
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/commits/abc").respond(
            json=_commit_detail_payload()
        )
        router.get(f"{API_BASE_URL}/users/octo").respond(json=_user_payload())
        router.get(f"{API_BASE_URL}/users/octo/repos").respond(
            json=[_repo_payload()]
        )
        router.get(f"{API_BASE_URL}/gists/abc123").respond(json=_gist_payload())
        router.get(f"{API_BASE_URL}/repos/octo/repo/actions/runs").respond(
            json={"workflow_runs": [_action_payload()]}
        )
        async with httpx.AsyncClient() as client:
            releases = await fetch_release_list(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
            release = await fetch_release(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "v1", 1.0
            )
            latest = await fetch_release_latest(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
            commits = await fetch_commit_list(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, None, 1.0
            )
            commit = await fetch_commit(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "abc", 1.0
            )
            user = await fetch_user_profile(
                client, _TOKEN, API_BASE_URL, "octo", 1.0
            )
            gist = await fetch_gist(client, _TOKEN, API_BASE_URL, "abc123", 1.0)
            actions = await fetch_actions(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
    assert releases.metadata == {"resource_type": "release_list", "count": 1}
    assert release.metadata == {"resource_type": "release", "tag": "v1"}
    assert latest.title == "Release v1 - octo/repo"
    assert commits.metadata == {
        "resource_type": "commit_list",
        "count": 1,
        "ref": None,
    }
    assert _result_metadata(commit)["resource_type"] == "commit"
    assert "Repositories" in user.content
    assert gist.metadata == {"resource_type": "gist", "file_count": 2}
    assert actions.metadata == {"resource_type": "actions", "run_count": 1}


def test_github_handler_renderer_edge_branches() -> None:
    issue = {
        "number": 1,
        "title": "No body",
        "state": "open",
        "user": {},
        "comments": 0,
    }
    issue_content = github_handlers._issue_content(issue, "", "", [])
    assert "## Comments" not in issue_content
    assert "| Labels | None |" in issue_content

    pr = {
        "number": 2,
        "title": "No files",
        "draft": False,
        "user": {},
        "base": {},
        "head": {},
        "changed_files": 0,
        "additions": 0,
        "deletions": 0,
    }
    pr_content = github_handlers._pull_request_content(pr, "open", [], True)
    assert "## Changed Files" not in pr_content
    assert "| Draft | No |" in pr_content

    release = github_handlers._release_detail(
        {"tag_name": "v0", "author": {}, "assets": []}, "octo", "repo"
    )
    assert "## Assets" not in release.content

    commit_content = github_handlers._commit_content(
        {"sha": "abcdef"},
        {"message": "Subject", "author": {}},
        {},
        [],
    )
    assert "## Changed Files" not in commit_content
    assert "unknown" in commit_content

    profile = github_handlers._user_profile_content(
        {
            "login": "octo",
            "type": "Organization",
            "public_repos": 0,
            "followers": 0,
            "following": 0,
        },
        [],
    )
    assert "## Repositories" not in profile

    gist = github_handlers._gist_content(
        {"owner": {}, "public": False},
        {"empty.txt": {"truncated": False}},
        "abc123",
    )
    assert "## empty.txt" in gist
    assert "`````" not in gist


async def test_github_file_directory_raw_and_wiki_handlers() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{API_BASE_URL}/repos/octo/repo/contents/src/app.py"
        ).respond(content="print('x')")
        router.get(
            f"{API_BASE_URL}/repos/octo/repo/contents/assets/logo.png"
        ).respond(
            json={
                "name": "logo.png",
                "size": 2048,
                "sha": "sha",
                "download_url": "https://download",
                "html_url": "https://github.com/octo/repo/blob/main/assets/logo.png",
            }
        )
        router.get(f"{API_BASE_URL}/repos/octo/repo/contents/src").respond(
            json=[
                {"name": "pkg", "type": "dir", "size": 0},
                {"name": "app.py", "type": "file", "size": 12},
            ]
        )
        router.get(
            "https://raw.githubusercontent.com/octo/repo/main/src/README.md"
        ).respond(content="# Package")
        router.get(
            "https://raw.githubusercontent.com/octo/repo/main/missing.py"
        ).respond(
            404,
            content="missing",
        )
        router.get(
            "https://raw.githubusercontent.com/wiki/octo/repo/Page.md"
        ).respond(content="wiki text")
        async with httpx.AsyncClient() as client:
            file_result = await fetch_file(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                None,
                "src/app.py",
                1.0,
            )
            binary = await fetch_file(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                "main",
                "assets/logo.png",
                1.0,
            )
            directory = await fetch_directory(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, None, "src", 1.0
            )
            raw = await fetch_raw_file(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                "main",
                "src/README.md",
                1.0,
            )
            with pytest.raises(ProviderError) as missing:
                await fetch_raw_file(
                    client,
                    _TOKEN,
                    API_BASE_URL,
                    _OWNER,
                    _REPO,
                    "main",
                    "missing.py",
                    1.0,
                )
            wiki = await fetch_wiki_page(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "Page", 1.0
            )
    assert _result_metadata(file_result)["resource_type"] == "file"
    assert _result_metadata(binary)["is_binary"] is True
    assert _result_metadata(directory)["item_count"] == 2
    assert raw.content == "# Package"
    assert missing.value.error_type is ErrorType.NOT_FOUND
    assert "wiki text" in wiki.content


async def test_github_file_handler_fallback_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolved_ambiguous(*args: object) -> FetchResult:
        return _simple_result("ambiguous")

    monkeypatch.setattr(
        github_handlers_file,
        "_resolve_ambiguous_ref_path",
        resolved_ambiguous,
    )
    async with httpx.AsyncClient() as client:
        file_ambiguous = await fetch_file(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "feature/src/app.py",
            None,
            1.0,
        )
        directory_ambiguous = await fetch_directory(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "feature/docs",
            None,
            1.0,
        )
    assert file_ambiguous.title == "ambiguous"
    assert directory_ambiguous.title == "ambiguous"

    async def fail_raw(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> str:
        raise RuntimeError(endpoint)

    async def fake_result_handler(*args: object) -> FetchResult:
        return _simple_result("fallback")

    monkeypatch.undo()
    monkeypatch.setattr(github_handlers_file, "github_get_raw", fail_raw)
    monkeypatch.setattr(
        github_handlers_file, "fetch_directory", fake_result_handler
    )
    async with httpx.AsyncClient() as client:
        directory_fallback = await fetch_file(
            client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "main", "src", 1.0
        )
    assert directory_fallback.title == "fallback"

    async def fail_directory(*args: object) -> FetchResult:
        raise ProviderError(ErrorType.NOT_FOUND, "missing directory", "github")

    monkeypatch.setattr(github_handlers_file, "fetch_directory", fail_directory)
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="/contents/src"):
            await fetch_file(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                "main",
                "src",
                1.0,
            )

    async def github_get_dict(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> dict[str, object]:
        return {"name": endpoint}

    monkeypatch.setattr(github_handlers_file, "github_get", github_get_dict)
    monkeypatch.setattr(github_handlers_file, "fetch_file", fake_result_handler)
    async with httpx.AsyncClient() as client:
        file_fallback = await fetch_directory(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "main",
            "README.md",
            1.0,
        )
    assert file_fallback.title == "fallback"

    async with httpx.AsyncClient() as client:
        with pytest.raises(ProviderError) as missing_ref:
            await fetch_raw_file(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                None,
                "README.md",
                1.0,
            )
    assert missing_ref.value.error_type is ErrorType.INVALID_INPUT


async def test_github_file_handler_root_readme_wiki_and_binary_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_overview(*args: object) -> FetchResult:
        return FetchResult(
            url="https://github.com/octo/repo",
            title="overview",
            content="# overview",
            source_provider="github",
            metadata={"default_branch": "main"},
        )

    monkeypatch.setattr(
        github_handlers_file, "fetch_repo_overview", fake_overview
    )
    async with httpx.AsyncClient() as client:
        overview = await fetch_raw_file(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "main",
            "README.md",
            1.0,
        )
    assert overview.title == "overview"

    async def broken_overview(*args: object) -> FetchResult:
        raise RuntimeError("overview failed")

    monkeypatch.setattr(
        github_handlers_file, "fetch_repo_overview", broken_overview
    )
    with respx.mock(assert_all_called=True) as router:
        router.get(
            "https://raw.githubusercontent.com/octo/repo/main/README.md"
        ).respond(content="# Raw Readme")
        async with httpx.AsyncClient() as client:
            raw_readme = await fetch_raw_file(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                "main",
                "README.md",
                1.0,
            )
    assert "Raw Readme" in raw_readme.content

    monkeypatch.setattr(
        github_handlers_file, "fetch_repo_overview", fake_overview
    )

    async def missing_wiki(
        client: httpx.AsyncClient,
        token: str,
        owner: str,
        repo: str,
        encoded_slug: str,
        extension: str,
        timeout_s: float,
    ) -> str | None:
        return None

    monkeypatch.setattr(github_handlers_file, "_fetch_wiki_raw", missing_wiki)
    async with httpx.AsyncClient() as client:
        wiki_fallback = await fetch_wiki_page(
            client, _TOKEN, API_BASE_URL, _OWNER, _REPO, "Missing", 1.0
        )
    assert wiki_fallback.title == "overview"

    async def github_get_list(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> list[dict[str, object]]:
        return [{"name": "nested", "type": "dir"}]

    async def fake_directory(*args: object) -> FetchResult:
        return _simple_result("binary-directory")

    monkeypatch.setattr(github_handlers_file, "github_get", github_get_list)
    monkeypatch.setattr(github_handlers_file, "fetch_directory", fake_directory)
    async with httpx.AsyncClient() as client:
        binary_directory = await fetch_file(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "main",
            "logo.png",
            1.0,
        )
    assert binary_directory.title == "binary-directory"

    async def github_get_binary_meta(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> dict[str, object]:
        return {"name": "logo.png", "size": 10, "sha": "abc"}

    monkeypatch.setattr(
        github_handlers_file, "github_get", github_get_binary_meta
    )
    async with httpx.AsyncClient() as client:
        binary = await fetch_file(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "main",
            "logo.png",
            1.0,
        )
    assert "**Download:**" not in binary.content
    assert _result_metadata(binary)["is_binary"] is True


async def test_github_ambiguous_ref_resolution_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def github_get_for_ambiguous_ref(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> dict[str, object]:
        if endpoint.endswith("/contents/src/app.py?ref=feature"):
            return {"name": "app.py"}
        raise RuntimeError(endpoint)

    async def fake_file(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        owner: str,
        repo: str,
        ref: str | None,
        path: str | None,
        timeout_s: float,
    ) -> FetchResult:
        return FetchResult(
            url=f"https://github.com/{owner}/{repo}/blob/{ref}/{path}",
            title=f"{ref}:{path}",
            content="# file",
            source_provider="github",
        )

    monkeypatch.setattr(
        github_handlers_file, "github_get", github_get_for_ambiguous_ref
    )
    monkeypatch.setattr(github_handlers_file, "fetch_file", fake_file)
    async with httpx.AsyncClient() as client:
        result = await github_handlers_file._resolve_ambiguous_ref_path(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "feature/src/app.py",
            "file",
            1.0,
        )
    assert result.title == "feature:src/app.py"

    async with httpx.AsyncClient() as client:
        directory_result = (
            await github_handlers_file._resolve_ambiguous_ref_path(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                "feature/src/app.py",
                "directory",
                1.0,
            )
        )
    assert directory_result.title == "feature:src/app.py"

    async def always_fail_github_get(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> dict[str, object]:
        raise RuntimeError(endpoint)

    async def fake_directory(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        owner: str,
        repo: str,
        ref: str | None,
        path: str | None,
        timeout_s: float,
    ) -> FetchResult:
        return FetchResult(
            url=f"https://github.com/{owner}/{repo}/tree/{ref}/{path}",
            title=f"{ref}:{path}",
            content="# directory",
            source_provider="github",
        )

    monkeypatch.setattr(
        github_handlers_file, "github_get", always_fail_github_get
    )
    monkeypatch.setattr(github_handlers_file, "fetch_directory", fake_directory)
    async with httpx.AsyncClient() as client:
        fallback = await github_handlers_file._resolve_ambiguous_ref_path(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "feature/docs",
            "directory",
            1.0,
        )
    assert fallback.title == "feature:docs"

    monkeypatch.setattr(github_handlers_file, "fetch_file", fake_file)
    async with httpx.AsyncClient() as client:
        file_fallback = await github_handlers_file._resolve_ambiguous_ref_path(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            "feature/src/app.py",
            "file",
            1.0,
        )
    assert file_fallback.title == "feature:src/app.py"


async def test_github_repo_overview_graphql_and_rest_fallback() -> None:
    gql_payload = _graphql_payload()
    rest_tree: dict[str, object] = {
        "tree": [
            {"path": "src", "type": "tree"},
            {"path": "src/app.py", "type": "blob", "size": 10},
            {"path": "docs", "type": "tree"},
            {"path": "docs/index.md", "type": "blob", "size": 20},
            {"path": "AGENTS.md", "type": "blob", "size": 10},
            {"path": "package.json", "type": "blob", "size": 2},
            {"path": ".cursor/rules/rule.md", "type": "blob", "size": 4},
        ]
    }
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{API_BASE_URL}/graphql").respond(json=gql_payload)
        router.get(
            f"{API_BASE_URL}/repos/octo/repo/git/trees/main:docs"
        ).respond(json={"tree": [{"path": "index.md", "type": "blob"}]})
        async with httpx.AsyncClient() as client:
            result = await fetch_repo_overview_gql(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
    assert _result_metadata(result)["graphql"] is True
    assert "Directory Structure" in result.content

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{API_BASE_URL}/graphql").respond(
            json={"errors": [{"message": "boom"}]}
        )
        _mock_rest_overview(router, rest_tree)
        async with httpx.AsyncClient() as client:
            result = await fetch_repo_overview(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
    assert _result_metadata(result)["graphql"] is False
    assert "Package Manifests" in result.content
    assert "AGENTS.md" in result.content

    with respx.mock(assert_all_called=True) as router:
        _mock_rest_overview(router, rest_tree)
        async with httpx.AsyncClient() as client:
            rest_result = await fetch_repo_overview_rest(
                client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
            )
    assert _result_metadata(rest_result)["default_branch"] == "main"


def test_github_markdown_builder_full_and_minimal_data() -> None:
    rich = RepoOverviewData(
        full_name="octo/repo",
        description="Repo description",
        owner=RepoOwner("octo", "https://github.com/octo", "User"),
        license=RepoLicense("MIT", "MIT"),
        visibility="PUBLIC",
        default_branch="main",
        created_at="2026-01-01T00:00:00Z",
        pushed_at="2026-01-02T00:00:00Z",
        is_fork=True,
        is_archived=False,
        fork_parent=ForkParent("base/repo", "https://github.com/base/repo"),
        disk_usage_bytes=1024,
        stars=10,
        forks=2,
        open_issues_count=1,
        open_prs_count=1,
        watchers=3,
        star_velocity="~1/day",
        topics=["ai"],
        features="issues, wiki",
        languages={"Python": 1024},
        tree_entries=[
            TreeEntry("src", "tree"),
            TreeEntry("src/app.py", "blob", 12),
        ],
        docs_dir_name="docs",
        docs_files=["index.md"],
        ai_rules_listing={".cursor/rules": [TextFile("rule.md", 4)]},
        ai_rules_inline={".windsurf/rules": TextFile("inline rule", 11)},
        dep_configs=[("Gemfile", "source")],
        readme=TextFile("# Readme", 8),
        context_files={
            "llms.txt": TextFile("llms", 4),
            "AGENTS.md": TextFile("agent notes", 11),
            "llms-full.txt": TextFile("full notes", 10),
        },
        too_large_context=["CLAUDE.md (101.0 KB - too large to inline)"],
        extra_detected=["CONTRIBUTING.md"],
        commits=[RepoCommit("2026-01-01T00:00:00Z", "Octo", "Subject")],
        monthly_commits=[("2026-01", 2)],
        issues=[
            RepoIssue(
                1,
                "Bug",
                "open",
                "octo",
                "`bug`",
                "2026-01-02T00:00:00Z",
                "Issue body",
            )
        ],
        pull_requests=[
            RepoPullRequest(
                2,
                "Change",
                "octo",
                "",
                "2026-01-02T00:00:00Z",
                True,
                "Pull request body. Second sentence.",
            )
        ],
        releases=[
            RepoRelease("", "v1", "2026-01-03T00:00:00Z", True, "Release")
        ],
        api_source="graphql",
        rate_limit_remaining=4999,
    )
    rich_result = build_repo_overview_result(rich)
    assert "Forked From" in rich_result.content
    assert "llms-full.txt" in rich_result.content
    assert ".windsurf/rules" in rich_result.content
    assert (
        github_markdown_builder._metadata(rich, True, 123)[
            "readme_original_tokens"
        ]
        == 123
    )
    assert github_markdown_builder._truncated_body("", 10) == ""
    assert _result_metadata(rich_result)["ai_context_files"] == [
        "llms.txt",
        "AGENTS.md",
        "llms-full.txt",
        ".windsurf/rules/",
        ".cursor/rules/",
        "CONTRIBUTING.md",
    ]

    minimal = RepoOverviewData(
        full_name="octo/minimal",
        description="_No description_",
        owner=RepoOwner("octo", "https://github.com/octo", "User"),
        license=None,
        visibility="PUBLIC",
        default_branch="main",
        created_at="",
        pushed_at="",
        is_fork=False,
        is_archived=False,
        fork_parent=None,
        disk_usage_bytes=0,
        stars=0,
        forks=0,
        open_issues_count=0,
        open_prs_count=0,
        watchers=0,
        star_velocity="",
        topics=[],
        features="",
        languages={},
        tree_entries=[],
        docs_dir_name=None,
        docs_files=[],
        ai_rules_listing={},
        ai_rules_inline={},
        dep_configs=[],
        readme=None,
    )
    minimal_result = build_repo_overview_result(minimal)
    assert "| License | None |" in minimal_result.content
    assert _result_metadata(minimal_result)["language"] is None
    assert "AI Context Files" not in minimal_result.content


async def test_github_repo_overview_helper_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{API_BASE_URL}/graphql").respond(
            json={"data": {"repository": None}}
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="Repository not found"):
                await fetch_repo_overview_gql(
                    client, _TOKEN, API_BASE_URL, _OWNER, _REPO, 1.0
                )

    async with httpx.AsyncClient() as client:
        no_children = await github_repo_overview._fetch_tree_children(
            client, _TOKEN, API_BASE_URL, _OWNER, _REPO, [], 1.0
        )
    assert no_children is None

    with respx.mock(assert_all_called=True) as router:
        router.post(f"{API_BASE_URL}/graphql").respond(
            500, json={"message": "boom"}
        )
        async with httpx.AsyncClient() as client:
            failed_children = await github_repo_overview._fetch_tree_children(
                client,
                _TOKEN,
                API_BASE_URL,
                _OWNER,
                _REPO,
                ["src"],
                1.0,
            )
    assert failed_children is None

    context, too_large = github_repo_overview._extract_gql_context_files(
        {"agents_md": {"byteSize": 200_000}}
    )
    assert context == {}
    assert too_large == ["AGENTS.md (195.3 KB - too large to inline)"]

    listing, inline = github_repo_overview._extract_gql_ai_rules(
        {
            "cursor_rules_dir": {
                "entries": [
                    {
                        "name": "rule.md",
                        "type": "blob",
                        "object": {"byteSize": 2},
                    }
                ]
            }
        }
    )
    assert listing == {".cursor/rules": [TextFile("rule.md", 2)]}
    assert inline == {}

    async def no_raw(
        client: httpx.AsyncClient,
        token: str,
        base_url: str,
        endpoint: str,
        timeout_s: float,
    ) -> str | None:
        return None

    monkeypatch.setattr(github_repo_overview, "github_get_raw_safe", no_raw)
    async with httpx.AsyncClient() as client:
        fetched_inline = await github_repo_overview._fetch_inline_ai_rules(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            {".cursor/rules": [TextFile("rule.md", 2)]},
            1.0,
        )
    assert fetched_inline == {}

    async with httpx.AsyncClient() as client:
        skipped_inline = await github_repo_overview._fetch_inline_ai_rules(
            client,
            _TOKEN,
            API_BASE_URL,
            _OWNER,
            _REPO,
            {".cursor/rules": [TextFile("one.md", 2), TextFile("two.md", 2)]},
            1.0,
        )
    assert skipped_inline == {}


def _mock_rest_overview(router: respx.Router, tree: dict[str, object]) -> None:
    router.get(f"{API_BASE_URL}/repos/octo/repo").respond(json=_repo_payload())
    router.get(f"{API_BASE_URL}/repos/octo/repo/readme").respond(
        content="# Readme"
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/languages").respond(
        json={"Python": 100}
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/commits").respond(
        json=[_commit_list_payload()]
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/issues").respond(
        json=[_issue_payload(), {**_issue_payload(), "pull_request": {}}]
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/pulls").respond(
        json=[_pr_payload()]
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/releases").respond(
        json=[_release_payload()]
    )
    router.get(
        f"{API_BASE_URL}/repos/octo/repo/git/trees/main?recursive=1"
    ).respond(json=tree)
    router.get(f"{API_BASE_URL}/repos/octo/repo/stargazers").respond(
        json=[
            {"starred_at": "2026-06-28T00:00:00Z"},
            {"starred_at": "2026-06-29T00:00:00Z"},
        ]
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/contents/AGENTS.md").respond(
        content="agent instructions"
    )
    router.get(f"{API_BASE_URL}/repos/octo/repo/contents/package.json").respond(
        content="{}"
    )
    router.get(
        f"{API_BASE_URL}/repos/octo/repo/contents/.cursor/rules/rule.md"
    ).respond(content="rule text")


def _issue_payload() -> dict[str, object]:
    return {
        "number": 1,
        "title": "Bug",
        "state": "open",
        "user": {"login": "octo"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": "2026-01-03T00:00:00Z",
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "dev"}],
        "comments": 1,
        "body": "Issue body",
        "html_url": "https://github.com/octo/repo/issues/1",
    }


def _comment_payload() -> dict[str, object]:
    return {
        "user": {"login": "reviewer"},
        "created_at": "2026-01-04T00:00:00Z",
        "body": "Comment body",
    }


def _pr_payload() -> dict[str, object]:
    return {
        "number": 5,
        "title": "Change",
        "state": "open",
        "draft": True,
        "user": {"login": "octo"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "merged_at": "2026-01-03T00:00:00Z",
        "base": {"ref": "main"},
        "head": {"ref": "feature"},
        "changed_files": 1,
        "additions": 10,
        "deletions": 2,
        "body": "Pull request body. More useful context.",
        "html_url": "https://github.com/octo/repo/pull/5",
    }


def _file_payload() -> dict[str, object]:
    return {
        "filename": "src/app.py",
        "status": "modified",
        "additions": 10,
        "deletions": 2,
        "patch": "+hello\n" + "x" * 3100,
    }


def _release_payload() -> dict[str, object]:
    return {
        "name": "",
        "tag_name": "v1",
        "published_at": "2026-01-01T00:00:00Z",
        "author": {"login": "octo"},
        "prerelease": True,
        "draft": True,
        "body": "Release body",
        "assets": [{"name": "asset.zip", "size": 2048, "download_count": 7}],
        "html_url": "https://github.com/octo/repo/releases/tag/v1",
    }


def _commit_list_payload() -> dict[str, object]:
    return {
        "sha": "abcdef123456",
        "commit": {
            "message": "Subject\nBody",
            "author": {"name": "Octo", "date": "2026-01-01T00:00:00Z"},
        },
    }


def _commit_detail_payload() -> dict[str, object]:
    return {
        **_commit_list_payload(),
        "html_url": "https://github.com/octo/repo/commit/abc",
        "commit": {
            "message": "Subject\nBody",
            "author": {
                "name": "Octo",
                "email": "octo@example.com",
                "date": "2026-01-01T00:00:00Z",
            },
        },
        "stats": {"additions": 10, "deletions": 2, "total": 12},
        "files": [_file_payload()],
    }


def _user_payload() -> dict[str, object]:
    return {
        "name": "Octo",
        "login": "octo",
        "type": "User",
        "bio": "Bio",
        "public_repos": 2,
        "followers": 3,
        "following": 4,
        "created_at": "2026-01-01T00:00:00Z",
        "html_url": "https://github.com/octo",
    }


def _repo_payload() -> dict[str, object]:
    return {
        "name": "repo",
        "full_name": "octo/repo",
        "description": "Repo description",
        "owner": {
            "login": "octo",
            "html_url": "https://github.com/octo",
            "type": "User",
        },
        "license": {"name": "MIT", "spdx_id": "MIT"},
        "visibility": "public",
        "default_branch": "main",
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-01-02T00:00:00Z",
        "fork": True,
        "archived": False,
        "parent": {
            "full_name": "base/repo",
            "html_url": "https://github.com/base/repo",
        },
        "size": 4,
        "stargazers_count": 2,
        "forks_count": 1,
        "open_issues_count": 2,
        "subscribers_count": 3,
        "watchers_count": 4,
        "topics": ["ai"],
        "has_issues": True,
        "has_wiki": True,
        "has_discussions": False,
        "has_projects": False,
        "has_pages": False,
        "html_url": "https://github.com/octo/repo",
        "language": "Python",
    }


def _gist_payload() -> dict[str, object]:
    return {
        "description": "",
        "owner": {"login": "octo"},
        "public": True,
        "created_at": "2026-01-01T00:00:00Z",
        "html_url": "https://gist.github.com/abc123",
        "files": {
            "main.py": {
                "language": "Python",
                "content": "print('hi')",
                "truncated": False,
            },
            "big.txt": {"size": 2048, "truncated": True},
        },
    }


def _action_payload() -> dict[str, object]:
    return {
        "conclusion": "failure",
        "status": "completed",
        "name": "CI",
        "head_branch": "main",
        "event": "push",
        "created_at": "2026-01-01T00:00:00Z",
    }


def _graphql_payload() -> dict[str, object]:
    repository = {
        "nameWithOwner": "octo/repo",
        "description": "Repo description",
        "owner": {
            "login": "octo",
            "url": "https://github.com/octo",
            "__typename": "User",
        },
        "licenseInfo": {"name": "MIT", "spdxId": "MIT"},
        "visibility": "PUBLIC",
        "defaultBranchRef": {
            "name": "main",
            "target": {
                "history": {
                    "nodes": [
                        {
                            "message": "Subject\nBody",
                            "committedDate": "2026-01-01T00:00:00Z",
                            "author": {"name": "Octo"},
                        }
                    ]
                },
                "m2026_01": {"totalCount": 2},
            },
        },
        "createdAt": "2026-01-01T00:00:00Z",
        "pushedAt": "2026-01-02T00:00:00Z",
        "isFork": True,
        "isArchived": False,
        "parent": {
            "nameWithOwner": "base/repo",
            "url": "https://github.com/base/repo",
        },
        "diskUsage": 4,
        "stargazerCount": 2,
        "forkCount": 1,
        "issues": {
            "totalCount": 1,
            "nodes": [
                {
                    "number": 1,
                    "title": "Bug",
                    "state": "OPEN",
                    "author": {"login": "octo"},
                    "labels": {"nodes": [{"name": "bug"}]},
                    "updatedAt": "2026-01-02T00:00:00Z",
                    "body": "Issue body",
                }
            ],
        },
        "pullRequests": {
            "totalCount": 1,
            "nodes": [
                {
                    "number": 2,
                    "title": "PR",
                    "author": {"login": "octo"},
                    "labels": {"nodes": []},
                    "updatedAt": "2026-01-02T00:00:00Z",
                    "isDraft": False,
                    "body": "Pull request sentence here. Another one here.",
                }
            ],
        },
        "watchers": {"totalCount": 3},
        "recent_stars": {
            "edges": [
                {"starredAt": "2026-06-28T00:00:00Z"},
                {"starredAt": "2026-06-29T00:00:00Z"},
            ]
        },
        "repositoryTopics": {"nodes": [{"topic": {"name": "ai"}}]},
        "hasIssuesEnabled": True,
        "hasWikiEnabled": True,
        "hasDiscussionsEnabled": False,
        "hasProjectsEnabled": False,
        "languages": {"edges": [{"size": 100, "node": {"name": "Python"}}]},
        "releases": {
            "nodes": [
                {
                    "name": "v1",
                    "tagName": "v1",
                    "publishedAt": "2026-01-01T00:00:00Z",
                    "isPrerelease": False,
                    "description": "Release",
                }
            ]
        },
        "rootTree": {
            "entries": [
                {"name": "src", "type": "tree"},
                {
                    "name": "README.md",
                    "type": "blob",
                    "object": {"byteSize": 10},
                },
                {"name": "docs", "type": "tree"},
            ]
        },
        "readme_0": {"text": "# Readme", "byteSize": 8},
        "agents_md": {"text": "agent notes", "byteSize": 11},
        "llms_txt": {"text": "llms", "byteSize": 4},
        "llms_full_txt": {"text": "full", "byteSize": 4},
        "cursor_rules_dir": {
            "entries": [
                {
                    "name": "rule.md",
                    "type": "blob",
                    "object": {"text": "rule", "byteSize": 4},
                }
            ]
        },
        "dep_package_json": {"text": "{}", "byteSize": 2},
        "contributing_md": {"byteSize": 1},
        "changelog_md": {"byteSize": 1},
    }
    return {
        "data": {"repository": repository, "rateLimit": {"remaining": 4999}}
    }
