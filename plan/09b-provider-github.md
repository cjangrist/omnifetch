# 09b — GitHub provider subpackage (`fetch/providers/github/`)

> The single most complex provider: ~2,000 LOC across 11 files. It does **no
> scraping** — it maps any `github.com` URL to a resource type and renders
> LLM-optimized markdown from the GitHub REST + GraphQL APIs. **API-native**
> (bypasses the failure gate, doc 05) and **breaker** provider for `github.com`,
> `gist.github.com`, `raw.githubusercontent.com`.
>
> Source files (port 1:1 into a Python subpackage):
>
> | TS file | lines | responsibility | I read it fully? |
> |---|---|---|---|
> | `index.ts` | 97 | entry + resource-type dispatch | ✅ |
> | `url-parser.ts` | 98 | URL → `ParsedGitHubUrl` | ✅ |
> | `types.ts` | — | `ParsedGitHubUrl` + response TypedDicts | partial |
> | `constants.ts` | 129 | reserved routes, API consts, limits | infer |
> | `api.ts` | 82 | REST GET helper (auth, headers, errors) | infer |
> | `graphql.ts` | 140 | GraphQL POST helper (discussions etc.) | infer |
> | `handlers.ts` | 354 | resource handlers (issue/PR/release/…) | infer |
> | `handlers-file.ts` | 232 | raw-file + blob fetching | infer |
> | `formatters.ts` | 224 | field/section formatting | infer |
> | `markdown-builder.ts` | 199 | markdown document assembly | infer |
> | `repo-overview.ts` | 404 | repo overview composite (README+meta+tree) | infer |
>
> **Implementer must read the four "infer" files** (`handlers`, `handlers-file`,
> `repo-overview`, `formatters`, `markdown-builder`) before porting — this doc
> gives the architecture, the exact dispatch contract, and the URL parser (both
> fully captured), which is enough to scope + verify the port but not to skip the
> read of the handler bodies.

---

## 09b.1 Entry + dispatch (`index.ts:20-93`) — exact contract

```
fetch_url(url):
  token = validate_api_key(GITHUB_API_KEY)
  parsed = parse_github_url(url)            # url-parser.ts
  if not parsed: raise INVALID_INPUT "Not a recognized GitHub URL"
  switch parsed.resource_type:  (dispatch table below)
  on any handler exception: handle_provider_error(...)
```

**Resource-type → handler dispatch (`index.ts:41-88`)** — port as a dict/`match`:

| resource_type | handler call |
|---|---|
| `repo_overview`, `wiki` | `fetch_repo_overview(token, owner, repo)` |
| `file` | `fetch_file(token, owner, repo, ref, path)` |
| `directory` | `fetch_directory(token, owner, repo, ref, path)` |
| `issue` | `fetch_issue(token, owner, repo, resource_id)` |
| `issue_list` | `fetch_issue_list(token, owner, repo)` |
| `pr_list` | `fetch_pr_list(token, owner, repo)` |
| `wiki_page` | `fetch_wiki_page(token, owner, repo, resource_id)` |
| `pull_request` | `fetch_pull_request(..., resource_id, include_files=False)` |
| `pr_files` | `fetch_pull_request(..., resource_id, include_files=True)` |
| `release_list` | `fetch_release_list(token, owner, repo)` |
| `release` | `fetch_release(token, owner, repo, resource_id)` |
| `release_latest` | `fetch_release_latest(token, owner, repo)` |
| `commit_list` | `fetch_commit_list(token, owner, repo, ref)` |
| `commit` | `fetch_commit(token, owner, repo, resource_id)` |
| `actions` | `fetch_actions(token, owner, repo)` |
| `user_profile`, `org_profile` | `fetch_user_profile(token, owner)` |
| `gist` | `fetch_gist(token, resource_id)` |
| `raw_file` | `fetch_raw_file(token, owner, repo, ref, path)` (handlers-file) |
| `compare`, `discussion`, `discussion_list`, `action_run`, default | **raise `INVALID_INPUT`** "not yet supported via API — falling through to scraper" (`index.ts:84-87`) |

> The `INVALID_INPUT` fallthrough is **load-bearing**: it lets the orchestrator
> drop a `github.com/owner/repo/compare/...` URL to the general scraper waterfall
> (doc 10 treats `INVALID_INPUT` as fall-through, `NOT_FOUND` as fast-fail).

---

## 09b.2 URL parser (`url-parser.ts`) — port verbatim

Fully captured. `parse_github_url(url) -> ParsedGitHubUrl | None`:
- `gist.github.com` → `parse_gist_url` (gist id must match `^[0-9a-f]+$`).
- `raw.githubusercontent.com` → `parse_raw_url` → `raw_file` (owner, repo, ref,
  path).
- non-`github.com` host → `None`.
- `orgs/<x>` → `org_profile`; single segment not in `RESERVED_ROUTES` →
  `user_profile`.
- `<owner>/<repo>` → `repo_overview`; else `parse_repo_subpath`.
- `parse_repo_subpath` (`:52-88`) maps the first sub-segment: `raw`, `issues`,
  `pulls`/`pull` (+ `.diff`/`.patch` strip, `/files` → `pr_files`), `wiki`,
  `releases` (`tag/<x>`, `latest`, list), `commits`/`commit`, `actions`
  (`runs/<id>` → `action_run`), `compare`, `discussions`, `blob`/`tree`/`blame`/
  `edit` → `parse_blob_tree_url`.
- `parse_blob_tree_url` (`:90-98`): `tree` → `directory`, else `file`; detects a
  40-hex SHA ref vs a branch ref.
- `RESERVED_ROUTES` (`:5-11`): 30 names (`trending, explore, settings, ...`) that
  are NOT usernames.
- `decode_segment` = `decodeURIComponent` with fallback → Python
  `urllib.parse.unquote` in a try/except.

Python: pure-function module `github/url_parser.py`, `ParsedGitHubUrl` as a frozen
dataclass (`resource_type: str`, `owner/repo/ref/path/resource_id: str | None`).

---

## 09b.3 Supporting layers (port mechanically)

- **`github/api.py`** (from `api.ts`): one REST GET helper — takes the **injected**
  `httpx.AsyncClient` (#6), sets `Authorization: Bearer <token>`,
  `Accept: application/vnd.github+json`, `X-GitHub-Api-Version`, `User-Agent`; calls
  `http_json(client, "github", …)`; maps GitHub error bodies. Read `api.ts` for the
  exact headers/version. `GitHubFetchProvider.fetch_url` threads `self._client` into
  every handler (no handler constructs its own client).
- **`github/graphql.py`** (from `graphql.ts`): POST to `/graphql` with a query +
  variables; used by handlers that REST can't serve cleanly. (Note: discussions
  are currently in the **unsupported/fallthrough** set, so confirm what graphql.ts
  is actually wired to before porting — it may back PR review threads or wikis.)
- **`github/handlers.py`** (from `handlers.ts`, 354): the 16 `fetch_*` resource
  handlers. Each: REST/GraphQL fetch → format via `formatters`/`markdown_builder`
  → `FetchResult` with `source_provider="github"`. These are the bulk of the work;
  read in full.
- **`github/handlers_file.py`** (from `handlers-file.ts`, 232): `fetch_raw_file`
  (and blob fetching) — base64-decodes GitHub contents API blobs, handles binary
  vs text, size limits. `raw.githubusercontent.com` 404 → likely `NOT_FOUND`
  (fast-fail; verify against the file to preserve the orchestrator's fast-fail
  semantics, doc 10).
- **`github/formatters.py`** + **`github/markdown_builder.py`**: pure string
  assembly → port directly, keep functions ≤45 lines (split where TS exceeds).
- **`github/repo_overview.py`** (404): the composite — README + repo metadata +
  top-level tree + languages, etc. Largest single file; will likely split into 2-3
  Python modules to respect the 500-line rule (it's under 500 but dense — judge at
  port time).
- **`github/constants.py`**: `RESERVED_ROUTES`, API base/version, truncation
  limits. Centralize as uppercase module constants (RULE_09 #2).

---

## 09b.4 Decisions / risks
- **HTTP reuse (#5)**: **all** GitHub calls go through `fetch/http`
  (`http_json`/`http_raw`) with provider name `"github"` → inherits the 5 MB cap,
  status mapping, redaction, per-host cap, and trace. **No second HTTP path** — the
  raw-file / wiki fetches (`handlers-file.ts:153/195`) that used a bare TS `fetch()`
  route through the shared client too (a 404 there maps per the NOT_FOUND note
  below). Guarded by the §13.5 grep test.
- **Rate limits**: GitHub 403 with `X-RateLimit-Remaining: 0` is a rate-limit, not
  an auth failure. The generic `http.py` maps 403 → `API_ERROR`. Check whether
  `api.ts` special-cases this; if so, port that nuance (it affects whether the
  orchestrator fast-fails or the user sees "access denied"). **Flag for the
  implementer to verify against `api.ts`.**
- **Typing**: GitHub JSON is deep; define `TypedDict`s (or Pydantic) in
  `github/types.py` for the response shapes the handlers touch — required for
  `mypy --strict` (no bare `dict[str, Any]` indexing without `.get`).
- **Largest porting effort** of any single provider — schedule accordingly; it is
  independent of all other providers and can proceed fully in parallel.

## 09b.5 Acceptance criteria
1. **URL parser parity**: a fixture table of ~40 real GitHub URLs (repo, blob with
   branch ref, blob with 40-hex SHA, tree, issue, issue_list, pull, pull/files,
   pull.diff, release tag, releases/latest, commit, commits/<branch>, gist (with +
   without user), raw.githubusercontent, user, org, wiki, wiki page, actions,
   actions/runs/<id> → `action_run`, compare, discussion) maps to the **exact**
   `resource_type` + fields the TS parser produces. (Pin against `url-parser.ts`.)
2. **Dispatch parity**: every supported `resource_type` routes to the correct
   handler; `compare`/`discussion`/`discussion_list`/`action_run`/unknown raise
   `ProviderError(INVALID_INPUT)` (→ orchestrator fall-through).
3. **Handler output**: for a recorded REST response per resource type, the rendered
   markdown matches the TS formatter output (golden files); `source_provider ==
   "github"`; result is API-native (a short gist isn't flagged as failure).
4. **Raw file**: `raw.githubusercontent.com/.../missing` → maps to the same error
   class the TS uses (verify NOT_FOUND vs API_ERROR against `handlers-file.ts`).
5. All github modules `mypy --strict` + ruff clean; `repo_overview` split if it
   trips the 500-line rule.

## 09b.6 Interfaces
**Exposes:** `GitHubFetchProvider` (ctor takes the injected client),
`parse_github_url`, the `fetch_*` handlers (each takes a `client` arg, #6).
**Consumes:** `fetch/http`, `fetch/util`, `fetch/types`, `httpx`, stdlib (`base64`,
`urllib.parse`).
