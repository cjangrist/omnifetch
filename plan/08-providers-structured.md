# 08 — Structured-extraction providers (zyte, diffbot, opengraph, scrappey)

> These 4 don't return a single markdown blob — they return structured fields that
> must be picked/assembled. Same uniform `fetch_url` skeleton as doc 07, but the
> response-mapping is provider-specific. `scrappey` is the **only** provider that
> uses `extract_html_title` (it returns raw HTML alongside innerText).

| name | method + path | auth | request | content ← | title ← | metadata | src lines |
|---|---|---|---|---|---|---|---|
| **zyte** | POST `{base}/v1/extract` | `Basic b64(key + ":")` | `{url, pageContent:true}` | `pageContent.itemMain` (req non-empty) | `pageContent.title ?? pageContent.headline ?? ""` | `{headline?, zyte_metadata?}` | zyte:24-63 |
| **diffbot** | GET `{base}/v3/article?token=&url=` | Q:`token` | `?token=&url=` (both url-encoded) | `objects[0].text` (req) | `objects[0].title ?? ""` | `{author?,date?,site_name?,image_count?}` | diffbot:22-56 |
| **opengraph** | GET `{base}/api/1.1/extract/{enc_url}?app_id=` | Q:`app_id` | path-encoded url + `?app_id=` | `concatenatedText` **or** `\n\n`.join(`tags[].innerText`) | `tags.find(tag in {title,h1}).innerText ?? ""` | `{response_code?, tag_count?}` | opengraph:20-60 |
| **scrappey** | POST `{base}/api/v1?key=` | Q:`key` | `{cmd:"request.get", url}` | `solution.innerText` (req `data.data == "success"`) | `extract_html_title(solution.response)` | — | scrappey:14-49 |

---

## 08.1 Per-provider detail

### zyte (`zyte/index.ts`)
- Auth: `Authorization: Basic ` + `basic_auth(key, "")` — i.e. `b64("<key>:")`
  (`:35`). The trailing colon matters; `basic_auth(key)` (default `password=""`)
  produces it.
- Body `{url, pageContent: true}` (`:37-40`).
- Map: `page = data.pageContent`; **fail if `not page or not page.itemMain`**
  (`:46`). `url = page.canonicalUrl ?? data.url ?? url` (`:51`). `title =
  page.title ?? page.headline ?? ""` (`:52`).
- Metadata is conditionally built (`:55-58`): include `headline` only if present,
  `zyte_metadata` only if `page.metadata` present. Use a dict built from present
  keys (don't emit `None` values) → matches the TS spread-if pattern.

### diffbot (`diffbot/index.ts`)
- GET URL with **both** `token` and `url` `encodeURIComponent`-encoded (`:26`).
  In Python pass via `params={"token": token, "url": url}` (httpx encodes), or
  `urllib.parse.quote`.
- Map: `article = data.objects[0]`; **fail if `not article.text`** (`:38`).
- Metadata built from present optional fields (`:47-52`): `author`, `date`,
  `site_name` (from `siteName`), `image_count` (from `images.length`).

### opengraph (`opengraph/index.ts`)
- Path-style: `{base}/api/1.1/extract/{quote(url)}?app_id={key}` (`:25`).
- Content priority (`:41-42`): `concatenatedText` if truthy, **else**
  `"\n\n".join(t["innerText"] for t in tags)`. Fail if both empty (`:36-46`).
- Title: first tag whose `tag` is `"title"` or `"h1"` → its `innerText`, else `""`
  (`:49,53`).
- Metadata `{response_code: requestInfo.responseCode, tag_count: len(tags)}`.

### scrappey (`scrappey/index.ts`) — uses HTML title
- POST to `{base}/api/v1?key={quote(key)}` (`:23`), body `{cmd:"request.get",
  url}` (`:26-29`).
- **Success check is unusual**: `data.data == "success"` AND `data.solution`
  present (`:33`). `data.data` is a status string, not a payload here.
- Content: `solution.innerText` (req, `:37-40`).
- Title: `extract_html_title(solution.response)` if `solution.response` present,
  else `""` (`:42`). **This is the one place `extract_html_title` (doc 01) is
  used** — `solution.response` is raw HTML.

---

## 08.2 Implementation notes
- Same skeleton as doc 07: each class **declares** `name`/`base_url`/`timeout_ms`/
  `required_secrets` (self-registers via `__init_subclass__`), then `validate_api_key`
  → `http_json` (httpx `json=` for POST bodies) → guard → `FetchResult` →
  `handle_provider_error`. All four use `http_json` (none use `http_text`).
- Build conditional metadata dicts with a comprehension over present keys, e.g.:
  ```python
  meta = {k: v for k, v in (("author", a.get("author")),
                            ("date", a.get("date")),
                            ("site_name", a.get("siteName")),
                            ("image_count", len(a["images"]) if a.get("images") else None))
          if v}
  metadata = meta or None
  ```
  (RULE_09 #10 comprehension; emits `None` only when empty → parity with TS
  `metadata?` being `undefined`.)

## 08.3 Acceptance criteria
1. **zyte**: recorded body with `pageContent.itemMain` → content=itemMain,
   url=canonicalUrl, title=title; missing `itemMain` → `ProviderError`; auth header
   equals `Basic ` + `b64("<key>:")`.
2. **diffbot**: `objects[0].text` mapped; metadata includes only present optional
   fields; `image_count` equals `len(images)`; empty `objects` → `ProviderError`.
3. **opengraph**: uses `concatenatedText` when present; falls back to joined tag
   innerText when not; title from first `title`/`h1` tag; both-empty → error.
4. **scrappey**: `data.data!="success"` → `ProviderError(... "request failed: ...")`;
   success → content=innerText, title from HTML `<title>` of `solution.response`.
5. Per-provider `respx` success + failure tests; 401/429 mapping inherited from
   doc 02.
6. `mypy --strict` + ruff clean.

## 08.4 Interfaces
**Exposes:** `ZyteFetchProvider`, `DiffbotFetchProvider`, `OpenGraphFetchProvider`,
`ScrappeyFetchProvider`. **Consumes:** `fetch/http`, `fetch/util`, `fetch/html`
(`extract_html_title`, `extract_markdown_title` not needed here), `fetch/types`.
