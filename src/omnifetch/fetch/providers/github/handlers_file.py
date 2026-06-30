"""GitHub file, directory, raw file, and wiki handlers."""

# ruff: noqa: E501, PLR0913, PLR2004

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from omnifetch.fetch.providers.github.api import (
    api_headers,
    github_get,
    github_get_raw,
)
from omnifetch.fetch.providers.github.formatters import (
    escape_table_cell,
    format_size,
    is_binary,
)
from omnifetch.fetch.providers.github.repo_overview import fetch_repo_overview
from omnifetch.fetch.shared.http import http_raw
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_README_RE = re.compile(r"^readme(\.[a-z0-9]+)?$", re.IGNORECASE)


async def fetch_file(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    ref: str | None,
    path: str | None,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub file URL as markdown."""
    if path is None and ref and "/" in ref:
        return await _resolve_ambiguous_ref_path(
            client, token, base_url, owner, repo, ref, "file", timeout_s
        )
    file_path = path or ""
    endpoint = _contents_endpoint(owner, repo, file_path, ref)
    if is_binary(file_path):
        return await _fetch_binary_file(
            client,
            token,
            base_url,
            owner,
            repo,
            file_path,
            ref,
            endpoint,
            timeout_s,
        )
    try:
        raw_content = await github_get_raw(
            client, token, base_url, endpoint, timeout_s
        )
    except Exception as error:
        try:
            return await fetch_directory(
                client, token, base_url, owner, repo, ref, path, timeout_s
            )
        except Exception:
            raise error from None
    return _file_result(owner, repo, file_path, ref, raw_content)


async def fetch_directory(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    ref: str | None,
    path: str | None,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub directory URL as markdown."""
    if path is None and ref and "/" in ref:
        return await _resolve_ambiguous_ref_path(
            client, token, base_url, owner, repo, ref, "directory", timeout_s
        )
    dir_path = path or ""
    result = await github_get(
        client,
        token,
        base_url,
        _contents_endpoint(owner, repo, dir_path, ref),
        timeout_s,
    )
    if isinstance(result, dict):
        return await fetch_file(
            client, token, base_url, owner, repo, ref, path, timeout_s
        )
    entries = (
        [entry for entry in result if isinstance(entry, dict)]
        if isinstance(result, list)
        else []
    )
    dirs = sorted(
        [entry for entry in entries if entry.get("type") == "dir"],
        key=lambda entry: str(entry.get("name", "")),
    )
    files = sorted(
        [entry for entry in entries if entry.get("type") != "dir"],
        key=lambda entry: str(entry.get("name", "")),
    )
    rows = [
        f"| dir | {escape_table_cell(str(entry.get('name', '')))}/ | - |"
        for entry in dirs
    ]
    rows.extend(
        f"| file | {escape_table_cell(str(entry.get('name', '')))} | {format_size(_size(entry))} |"
        for entry in files
    )
    content = (
        f"# Directory: {dir_path or '/'}\n\n"
        f"**Repository:** {owner}/{repo}\n**Branch:** `{ref or 'default'}`\n"
        f"**Items:** {len(entries)}\n\n"
        "| Type | Name | Size |\n|------|------|------|\n"
        + "\n".join(rows)
        + "\n\n---\n*Fetched via GitHub API*\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/tree/{ref or 'main'}/{dir_path}",
        title=f"{dir_path or '/'} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={
            "resource_type": "directory",
            "path": dir_path,
            "ref": ref,
            "item_count": len(entries),
        },
    )


async def fetch_raw_file(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    ref: str | None,
    path: str | None,
    timeout_s: float,
) -> FetchResult:
    """Fetch a raw.githubusercontent.com file URL."""
    if not ref:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"Raw URL missing ref: {owner}/{repo}",
            "github",
        )
    if _is_root_readme(path):
        overview = await _try_root_readme_overview(
            client, token, base_url, owner, repo, ref, timeout_s
        )
        if overview:
            return overview
    raw_url = _raw_url(owner, repo, ref, path)
    raw, status = await http_raw(
        client,
        "github",
        raw_url,
        headers=api_headers(token),
        timeout_s=timeout_s,
        expected_statuses=(404,),
    )
    if status == 404:
        raise ProviderError(
            ErrorType.NOT_FOUND, f"Not found: {raw_url}", "github"
        )
    return _raw_file_result(owner, repo, ref, path, raw)


async def fetch_wiki_page(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    page_slug: str,
    timeout_s: float,
) -> FetchResult:
    """Fetch a GitHub wiki page from raw wiki storage."""
    title = page_slug.replace("-", " ")
    encoded_slug = "/".join(quote(part) for part in page_slug.split("/"))
    for extension in (".md", "", ".mediawiki", ".asciidoc", ".rst"):
        raw = await _fetch_wiki_raw(
            client, token, owner, repo, encoded_slug, extension, timeout_s
        )
        if raw:
            return FetchResult(
                url=f"https://github.com/{owner}/{repo}/wiki/{page_slug}",
                title=f"{title} - {owner}/{repo} Wiki",
                content=f"# {title}\n\n**Wiki page** from [{owner}/{repo}](https://github.com/{owner}/{repo})\n\n---\n\n{raw}\n",
                source_provider="github",
                metadata={"resource_type": "wiki_page"},
            )
    return await fetch_repo_overview(
        client, token, base_url, owner, repo, timeout_s
    )


async def _resolve_ambiguous_ref_path(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    combined: str,
    resource_type: str,
    timeout_s: float,
) -> FetchResult:
    parts = combined.split("/")
    try_order = [0, *range(len(parts) - 1, 0, -1)]
    for index in dict.fromkeys(try_order):
        try_ref = "/".join(parts[: index + 1])
        try_path = "/".join(parts[index + 1 :])
        try:
            await github_get(
                client,
                token,
                base_url,
                _contents_endpoint(owner, repo, try_path, try_ref),
                timeout_s,
            )
        except Exception:
            continue
        if resource_type == "file":
            return await fetch_file(
                client,
                token,
                base_url,
                owner,
                repo,
                try_ref,
                try_path,
                timeout_s,
            )
        return await fetch_directory(
            client, token, base_url, owner, repo, try_ref, try_path, timeout_s
        )
    if resource_type == "file":
        return await fetch_file(
            client,
            token,
            base_url,
            owner,
            repo,
            parts[0],
            "/".join(parts[1:]),
            timeout_s,
        )
    return await fetch_directory(
        client,
        token,
        base_url,
        owner,
        repo,
        parts[0],
        "/".join(parts[1:]),
        timeout_s,
    )


async def _fetch_binary_file(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    file_path: str,
    ref: str | None,
    endpoint: str,
    timeout_s: float,
) -> FetchResult:
    meta = await github_get(client, token, base_url, endpoint, timeout_s)
    if isinstance(meta, list):
        return await fetch_directory(
            client, token, base_url, owner, repo, ref, file_path, timeout_s
        )
    name = (
        str(meta.get("name", file_path))
        if isinstance(meta, dict)
        else file_path
    )
    size = _size(meta) if isinstance(meta, dict) else 0
    sha = str(meta.get("sha", "")) if isinstance(meta, dict) else ""
    download_url = (
        str(meta.get("download_url", "")) if isinstance(meta, dict) else ""
    )
    content = f"# {name}\n\n**Type:** Binary file\n**Size:** {format_size(size)}\n**SHA:** `{sha}`\n\nThis is a binary file that cannot be displayed as text.\n"
    if download_url:
        content += f"\n**Download:** [{name}]({download_url})\n"
    return FetchResult(
        url=str(meta.get("html_url", ""))
        if isinstance(meta, dict)
        else f"https://github.com/{owner}/{repo}/blob/{ref or 'main'}/{file_path}",
        title=f"{file_path} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "file", "is_binary": True, "size": size},
    )


async def _try_root_readme_overview(
    client: httpx.AsyncClient,
    token: str,
    base_url: str,
    owner: str,
    repo: str,
    ref: str,
    timeout_s: float,
) -> FetchResult | None:
    try:
        overview = await fetch_repo_overview(
            client, token, base_url, owner, repo, timeout_s
        )
    except Exception:
        return None
    default_branch = (
        overview.metadata.get("default_branch") if overview.metadata else None
    )
    return overview if ref == default_branch else None


async def _fetch_wiki_raw(
    client: httpx.AsyncClient,
    token: str,
    owner: str,
    repo: str,
    encoded_slug: str,
    extension: str,
    timeout_s: float,
) -> str | None:
    raw_url = f"https://raw.githubusercontent.com/wiki/{owner}/{repo}/{encoded_slug}{extension}"
    raw, status = await http_raw(
        client,
        "github",
        raw_url,
        headers=api_headers(token),
        timeout_s=timeout_s,
        expected_statuses=(404,),
    )
    return raw if status != 404 and raw else None


def _contents_endpoint(
    owner: str,
    repo: str,
    path: str,
    ref: str | None,
) -> str:
    encoded_path = "/".join(quote(part) for part in path.split("/") if part)
    ref_param = f"?ref={quote(ref)}" if ref else ""
    return f"/repos/{owner}/{repo}/contents/{encoded_path}{ref_param}"


def _file_result(
    owner: str,
    repo: str,
    file_path: str,
    ref: str | None,
    raw_content: str,
) -> FetchResult:
    file_ext = file_path.rsplit(".", maxsplit=1)[-1] if "." in file_path else ""
    content = (
        f"# {file_path or 'File'}\n\n"
        f"**Repository:** {owner}/{repo}\n**Branch:** `{ref or 'default'}`\n"
        f"**Size:** {format_size(len(raw_content))}\n\n---\n\n"
        f"`````{file_ext}\n{raw_content}\n`````\n\n---\n"
        "*Fetched via GitHub API*\n"
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/blob/{ref or 'main'}/{file_path}",
        title=f"{file_path} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "file", "path": file_path, "ref": ref},
    )


def _raw_file_result(
    owner: str,
    repo: str,
    ref: str,
    path: str | None,
    raw: str,
) -> FetchResult:
    file_name = path.rsplit("/", maxsplit=1)[-1] if path else ref
    file_ext = path.rsplit(".", maxsplit=1)[-1] if path and "." in path else ""
    content = (
        raw
        if path and "/" in path and _is_readme_file_name(file_name)
        else (
            f"# {file_name}\n\n**Repository:** {owner}/{repo}\n**Ref:** `{ref}`\n"
            f"**Size:** {format_size(len(raw))}\n\n---\n\n`````{file_ext}\n{raw}\n`````\n\n"
            "---\n*Fetched via GitHub raw URL*\n"
        )
    )
    return FetchResult(
        url=f"https://github.com/{owner}/{repo}/blob/{ref}/{path or ''}",
        title=f"{path or ref} - {owner}/{repo}",
        content=content,
        source_provider="github",
        metadata={"resource_type": "raw_file", "path": path, "ref": ref},
    )


def _raw_url(owner: str, repo: str, ref: str, path: str | None) -> str:
    return (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        if path
        else f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}"
    )


def _is_root_readme(path: str | None) -> bool:
    return bool(path and "/" not in path and _is_readme_file_name(path))


def _is_readme_file_name(file_name: str) -> bool:
    return bool(_README_RE.fullmatch(file_name))


def _size(entry: object) -> int:
    return (
        int(entry.get("size", 0))
        if isinstance(entry, dict) and isinstance(entry.get("size"), int)
        else 0
    )
