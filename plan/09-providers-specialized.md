# 09 — Specialized providers: supadata, serpapi, sociavault, kimi

> Domain-specific providers with non-uniform control flow: async job polling,
> platform routing, and an outbound proxy. (GitHub is large enough to get its own
> doc, `09b-provider-github.md`.)

---

## 09.1 supadata — YouTube transcripts with async-job fallback

Source: `supadata/index.ts` (144). Auth: `H: x-api-key`. **API-native** (bypasses
the failure length/pattern gate, doc 05).

Flow:
1. `extract_video_id(url)` (`:26-47`) — handles `youtu.be/<id>`,
   `youtube.com/watch?v=<id>`, `/embed|/shorts|/live/<id>`. Not a YT URL → raise.
2. GET `{base}/youtube/transcript?url=<url>&text=true&mode=auto&lang=en` with
   `x-api-key` (`:90-104`). **Uses a raw response** (not `http_json`) because it
   must branch on HTTP **202**.
3. **202 → async job**: body `{jobId}`; `poll_job(api_key, jobId, timeout)`
   (`:49-75`) polls `GET {base}/youtube/transcript/{jobId}` every **1.5 s**, each
   poll bounded by 10 s, until `status=="completed"` (return `content`),
   `status=="failed"` (raise), or the overall `timeout` deadline (raise).
4. Non-202 ok → `{content,...}`; fail if empty.
5. Result: `title=f"YouTube Transcript: {video_id}"`,
   `content=f"# YouTube Video Transcript\n\n{content}"`.

Python notes:
- The 202 branch needs the **status code**: use
  `http_raw(self._client, "supadata", url, expected_statuses=(202,))` (doc 02 §02.2)
  — the **shared** client, never a bare request (#5) — then `json.loads(raw)` and
  branch on the returned status.
- `poll_job` → an `async` loop using `asyncio.get_running_loop().time()` for the
  deadline and `await asyncio.sleep(1.5)` between polls. Wrap the whole
  `fetch_url` in `provider_timeout(self.timeout_ms)` (60 s) as the hard ceiling.
- Keep `extract_video_id` as a module function (shared with serpapi — put it in a
  small `fetch/providers/_youtube.py` to avoid duplication; both supadata and
  serpapi have an identical copy in TS).

## 09.2 serpapi — YouTube transcripts (EXPLICIT-ONLY)

Source: `serpapi/index.ts` (105). Auth: `Q: api_key`. **Not in the waterfall or
any breaker** — reachable only via explicit `provider:"serpapi"` (see overview
§0.6). Keep it registered + active-when-keyed, never auto-selected.

Flow:
1. `extract_video_id(url)` (identical to supadata; share `_youtube.py`).
2. GET `{base}?engine=youtube_video_transcript&v=<id>&api_key=<k>` (`:59-72`).
3. `data.error` → raise; empty `transcript` → raise (`:74-80`).
4. `transcript_text = " ".join(t["snippet"] for t in transcript)` (`:82`).
5. Result: `title=f"YouTube Transcript: {video_id}"`,
   `content=f"# YouTube Video Transcript\n\n{transcript_text}"`,
   `metadata={"video_id":video_id, "transcript_segments":len(transcript)}`.

> serpapi is **not** API-native (not in `_API_NATIVE_PROVIDERS`), so its transcript
> still passes the 200-char gate — fine for real transcripts.

> **Typing fidelity (#2) — required normalization.** In TS the no-video-id branch
> (`serpapi/index.ts:53-56`) throws a **plain `Error`** *before* its try/catch, so it
> carries no `ErrorType`. The Python port MUST raise
> `ProviderError(ErrorType.INVALID_INPUT, f"Not a YouTube URL: {url[:200]}", "serpapi")`
> instead — so an explicit `provider:"serpapi"` call on a non-YouTube URL is typed
> (REST→400, MCP→typed error) and falls through cleanly rather than surfacing an
> unattributed exception. **supadata** has the identical plain-`Error` shape
> (`supadata/index.ts:85-87`) — type it the same way.

## 09.3 sociavault — social-media platform routing

Source: `sociavault/index.ts` (135). Auth: `H: X-API-Key`. Breaker provider for
`social_media`. `timeout=15000` (data-tuned, `env.ts:176-179`).

Flow:
1. `PLATFORM_ROUTES` (`:16-35`): 9 routes mapping host-sets → `{platform,
   endpoint, param_name}`. Port verbatim:
   - reddit → `/v1/scrape/reddit/post/comments`
   - twitter/x → `/v1/scrape/twitter/tweet`
   - youtube → `/v1/scrape/youtube/video`
   - facebook/fb → `/v1/scrape/facebook/post`
   - instagram → `/v1/scrape/instagram/post-info`
   - tiktok → `/v1/scrape/tiktok/video-info`
   - linkedin → `/v1/scrape/linkedin/post`
   - threads → `/v1/scrape/threads/post`
   - pinterest → `/v1/scrape/pinterest/pin`
2. `detect_route(url)` matches `hostname` (lowercased) against route `hosts`
   (`:37-47`). No match → `ProviderError(INVALID_INPUT, "SociaVault only supports
   social media URLs (...)")` (`:63-69`) — orchestrator **falls through** to the
   next provider (INVALID_INPUT is fall-through, not fast-fail; doc 10).
3. GET `{base}{endpoint}?{param_name}={url}` with `X-API-Key` (`:76-89`).
4. `data.success` false → raise (`:91`).
5. `content = format_social_content(platform, data.data)` (`:113-131`): builds
   `# {platform} content` + `**Title-Cased Key:** value` lines; values stringified
   (str/num/bool direct, arrays joined, objects JSON-pretty). Port the
   `stringify_value` recursion + the key Title-Casing (`key.replace("_"," ")` then
   capitalize each word).
6. Result: `title=f"{platform} content"`, `metadata={"platform", "credits_used":
   data.creditsUsed}`.

**Breaker/route mismatch (#3) — preserve exactly.** `snapchat.com` is in the
`social_media` breaker domains (doc 10 §10.1) but has **no** `PLATFORM_ROUTES` entry.
So a snapchat URL triggers the breaker, `detect_route` returns no match, sociavault
raises `ProviderError(INVALID_INPUT)`, and the orchestrator **falls through** to the
general waterfall (INVALID_INPUT = fall-through, not fast-fail). This is intentional
in the TS source — do **not** "fix" it by inventing a snapchat route.

Python notes:
- `PLATFORM_ROUTES` → a module-level tuple of frozen dataclasses;
  build a `{host: route}` dict once for O(1) lookup.
- `format_social_content` → keep ≤45 lines; `stringify_value` recursion mirrors
  `:116-122`.

## 09.4 kimi — Scrapfly-proxied coding API

Source: `kimi/index.ts` (73) + `providers/search/kimi/scrapfly_proxy.ts` (75) +
`providers/search/kimi/headers.ts` (42). Auth: `KIMI_API_KEY` (+ requires
`SCRAPFLY_API_KEY`). `timeout=60000`.

**Why proxied**: `api.kimi.com` has a Cloudflare WAF rule that blocks
datacenter-ASN egress; Scrapfly forwards the request from a residential/browser
fingerprint (`scrapfly_proxy.ts:1-9`). The TS comment notes this was needed for
Workers' ASN; on a self-hosted Python box you **may not need the proxy** — but
**port it for parity** and make it the default path. (Optional enhancement: a
`KIMI_DIRECT=1` env to skip Scrapfly when your egress isn't blocked.)

Port two helpers into `fetch/providers/kimi_proxy.py`:
- `build_kimi_fetch_headers(api_key)` (`headers.ts:34-41`): identity headers
  spoofing Kimi CLI — `User-Agent: KimiCLI/1.37.0`, `Authorization: Bearer <k>`,
  `Content-Type`/`Accept: application/json`, `X-Msh-Tool-Call-Id: fetch-<rand12>`,
  plus the 6 `X-Msh-*` device headers (`:14-21`, hardcoded constants — port
  verbatim). The `new_tool_call_id` randomness → `uuid4().hex[:12]`.
- `proxy_post_via_scrapfly(provider, target_url, headers, body, timeout)`
  (`scrapfly_proxy.ts:42-75`): builds a Scrapfly `/scrape` GET URL with
  `key, url=target, method=POST, country=us`, and **each target header injected as
  `headers[<name>]=<value>` query params** (`:36-39`); POSTs the body; expects
  `data.result.{status_code, content, response_headers}`; returns `(status, body,
  headers)`. Validate `result` present + `status_code` numeric (`:62-68`).

`kimi.fetch_url` flow (`:31-67`):
1. `key = validate_api_key(KIMI_API_KEY)`.
2. `proxied = await proxy_post_via_scrapfly(name, f"{base}/coding/v1/fetch",
   build_kimi_fetch_headers(key), json.dumps({"url": url}), timeout)`.
3. `if not 200<=proxied.status<300: raise ProviderError(PROVIDER_ERROR, ...)`.
4. `data = json.loads(proxied.body)`; `content = (data.get("markdown") or "").strip()`;
   empty → raise.
5. Result: `url=data.get("url") or url`, `title=(data.get("title") or "").strip()
   or extract_markdown_title(content)`.

Python notes:
- `proxy_post_via_scrapfly(client, …)` receives the **injected** client
  (`self._client`, #6) and reuses `http_json` with the **scrapfly** provider name
  for trace attribution; its key is `secrets.scrapfly_api_key` (so kimi depends on
  scrapfly being keyed — surfaced as `INVALID_INPUT` at call time, which the
  waterfall treats as fall-through).
- Inject header values into query params with proper encoding.

---

## 09.5 Acceptance criteria
1. **supadata**: a 200 transcript body → `# YouTube Video Transcript\n\n...`,
   title `YouTube Transcript: <id>`; a **202** → poll path resolves on
   `status=="completed"`; `status=="failed"` raises; deadline exceeded raises.
   `extract_video_id` covers `youtu.be`, `watch?v=`, `/shorts/`, `/embed/`,
   `/live/`; non-YT URL raises. Output is **API-native** (50-char transcript not
   flagged as failure by doc 05).
2. **serpapi**: explicit-only — assert it is in `active_names` (when keyed) but
   **absent from the waterfall config** (doc 10 test); transcript snippets joined
   by single spaces; `metadata.transcript_segments == len(transcript)`; a
   **non-YouTube URL raises `ProviderError(INVALID_INPUT)`** (#2 — typed, not a bare
   `Error`); same for supadata.
3. **sociavault**: each of the 9 host families routes to the right endpoint; an
   unsupported host raises `ProviderError(INVALID_INPUT)`; **`snapchat.com` (breaker
   domain, no route) raises `INVALID_INPUT` → falls through** (#3);
   `format_social_content` renders `**Key:** value` lines with Title-Cased keys and
   recursively stringified values; metadata carries `platform` + `credits_used`.
4. **kimi**: `build_kimi_fetch_headers` emits all `X-Msh-*` headers + a fresh
   `X-Msh-Tool-Call-Id`; `proxy_post_via_scrapfly` builds a Scrapfly URL whose
   query contains `headers[Authorization]=Bearer <k>` and `method=POST`; a proxied
   non-2xx upstream status raises `PROVIDER_ERROR`; success maps markdown→content.
5. Shared `_youtube.extract_video_id` used by both supadata + serpapi (no dup).
6. `mypy --strict` + ruff clean.

## 09.6 Interfaces
**Exposes:** `SupadataFetchProvider`, `SerpapiFetchProvider`,
`SociaVaultFetchProvider`, `KimiFetchProvider`, `kimi_proxy.*`,
`_youtube.extract_video_id`. All four take the **injected** client at construction
(#6); the `kimi_proxy` + supadata-poll helpers take a `client` param; supadata's
202 uses `http_raw` (#5). **Consumes:** `fetch/http` (`http_json/http_raw`,
client-first), `fetch/util`, `fetch/html`, `fetch/config` (scrapfly key for kimi),
`fetch/types`, `httpx`.
