"""GitHub fetch provider dispatch."""

# ruff: noqa: PLR0911, PLR0912

from __future__ import annotations

from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.providers.github.constants import (
    API_BASE_URL,
    API_KEY_ENV_NAME,
    TIMEOUT_MS,
)
from omnifetch.fetch.providers.github.handlers import (
    fetch_actions,
    fetch_commit,
    fetch_commit_list,
    fetch_directory,
    fetch_file,
    fetch_gist,
    fetch_issue,
    fetch_issue_list,
    fetch_pr_list,
    fetch_pull_request,
    fetch_raw_file,
    fetch_release,
    fetch_release_latest,
    fetch_release_list,
    fetch_repo_overview,
    fetch_user_profile,
    fetch_wiki_page,
)
from omnifetch.fetch.providers.github.types import ParsedGitHubUrl
from omnifetch.fetch.providers.github.url_parser import parse_github_url
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import handle_provider_error, validate_api_key


class GitHubFetchProvider(FetchProvider):
    """Fetch GitHub resources through GitHub REST and GraphQL APIs."""

    name = "github"
    description = (
        "Fetch GitHub content via REST and GraphQL APIs. Returns structured, "
        "LLM-optimized markdown for repos, files, issues, PRs, and more."
    )
    base_url = API_BASE_URL
    timeout_ms = TIMEOUT_MS
    required_secrets = (API_KEY_ENV_NAME,)

    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` through GitHub APIs and return normalized markdown."""
        validate_api_key(self._secrets.get(API_KEY_ENV_NAME), self.name)
        parsed = parse_github_url(url)
        if parsed is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                f"Not a recognized GitHub URL: {url}",
                self.name,
            )

        try:
            return await self._dispatch(parsed)
        except Exception as error:
            handle_provider_error(
                error, self.name, f"fetch {parsed.resource_type}"
            )

    async def _dispatch(self, parsed: ParsedGitHubUrl) -> FetchResult:
        token = validate_api_key(self._secrets.get(API_KEY_ENV_NAME), self.name)
        owner = parsed.owner or ""
        repo = parsed.repo or ""
        resource_id = parsed.resource_id or ""
        match parsed.resource_type:
            case "repo_overview" | "wiki":
                return await fetch_repo_overview(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    self.timeout_s,
                )
            case "file":
                return await fetch_file(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    parsed.ref,
                    parsed.path,
                    self.timeout_s,
                )
            case "directory":
                return await fetch_directory(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    parsed.ref,
                    parsed.path,
                    self.timeout_s,
                )
            case "issue":
                return await fetch_issue(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    resource_id,
                    self.timeout_s,
                )
            case "issue_list":
                return await fetch_issue_list(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    self.timeout_s,
                )
            case "pr_list":
                return await fetch_pr_list(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    self.timeout_s,
                )
            case "wiki_page":
                return await fetch_wiki_page(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    resource_id,
                    self.timeout_s,
                )
            case "pull_request":
                return await fetch_pull_request(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    resource_id,
                    False,
                    self.timeout_s,
                )
            case "pr_files":
                return await fetch_pull_request(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    resource_id,
                    True,
                    self.timeout_s,
                )
            case "release_list":
                return await fetch_release_list(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    self.timeout_s,
                )
            case "release":
                return await fetch_release(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    resource_id,
                    self.timeout_s,
                )
            case "release_latest":
                return await fetch_release_latest(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    self.timeout_s,
                )
            case "commit_list":
                return await fetch_commit_list(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    parsed.ref,
                    self.timeout_s,
                )
            case "commit":
                return await fetch_commit(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    resource_id,
                    self.timeout_s,
                )
            case "actions":
                return await fetch_actions(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    self.timeout_s,
                )
            case "user_profile" | "org_profile":
                return await fetch_user_profile(
                    self._client, token, self.base_url, owner, self.timeout_s
                )
            case "gist":
                return await fetch_gist(
                    self._client,
                    token,
                    self.base_url,
                    resource_id,
                    self.timeout_s,
                )
            case "raw_file":
                return await fetch_raw_file(
                    self._client,
                    token,
                    self.base_url,
                    owner,
                    repo,
                    parsed.ref,
                    parsed.path,
                    self.timeout_s,
                )
            case _:
                raise ProviderError(
                    ErrorType.INVALID_INPUT,
                    "GitHub resource type "
                    f"'{parsed.resource_type}' not yet supported via API - "
                    "falling through to scraper",
                    self.name,
                )
