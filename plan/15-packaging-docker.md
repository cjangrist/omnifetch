# 15 — Packaging: Docker & docker-compose

> Containerizes the finished server so it runs **identically anywhere** — the
> concrete expression of the cloud-agnostic posture (overview §0.1). One image,
> two transports (stdio for MCP clients, HTTP for the REST `/fetch` + streamable
> MCP), an optional Redis for the shared cache. No cloud-vendor APIs anywhere.
>
> Independent work package — do it any time after doc 11. New files live at the
> **repo root**: `Dockerfile`, `.dockerignore`, `docker-compose.yml`.

---

## 15.1 Why this exists (cloud-agnostic, #3)
`omnisearch` deployed via `wrangler` to Cloudflare — vendor-locked. `omnifetch` is
a plain Python process, so packaging is a standard OCI image that runs on Docker,
Podman, any Kubernetes, Fly, Render, ECS, Cloud Run, a Raspberry Pi — anywhere a
container runs. The image is the portable, reproducible unit; compose is the local
/ small-deploy convenience. Nothing here assumes a specific cloud.

---

## 15.2 `Dockerfile` — multi-stage `uv` build
Mirror Astral's official `uv` Docker guidance (verify against current uv docs,
RULE_04). Build deps in a cached layer, ship a slim non-root runtime.

```dockerfile
# ---- build: resolve + install into a venv with uv (layer-cached on lockfile) ----
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# deps first (cache layer): no project, no dev, include the perf extra (uvloop)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra performance
# then the project
COPY . .
RUN uv sync --frozen --no-dev --extra performance

# ---- runtime: slim, non-root, just the venv + source ----
FROM python:3.13-slim AS runtime
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    OMNIFETCH_TRANSPORT=stdio
USER app
WORKDIR /app
# Default = stdio (an MCP client attaches via `docker run -i`). Compose overrides
# the command for HTTP. The console script is the entrypoint (pyproject [scripts]).
ENTRYPOINT ["omnifetch"]
```
- **Pin** the base image digests for reproducibility (supply-chain hygiene; the
  repo already SHA-pins CI actions per `SECURITY.md`). `--frozen` enforces the
  committed `uv.lock`.
- The `performance` extra pulls **uvloop** (POSIX-only marker, doc 14 §14.11);
  in-container Linux always gets it.
- Non-root `app` user; nothing writes outside the venv except the optional
  `disk` cache path (mount a volume if `OMNIFETCH_CACHE_BACKEND=disk`).

## 15.3 `.dockerignore`
```
.git
.venv
**/__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
tests
trash
tmp
plan
*.md
!README.md
```
(Keep the build context tiny + deterministic; `plan/` and scratch dirs never ship.)

---

## 15.4 `docker-compose.yml` — HTTP service (+ optional Redis)
HTTP transport exposes both the streamable MCP endpoint and the lightweight REST
`/fetch` (doc 11 §11.5). Provider keys come from `.env` (never baked into the
image). Redis is an **opt-in profile** for the shared/persistent cache backend.

```yaml
services:
  omnifetch:
    build: .
    image: omnifetch:local
    command: ["--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
    ports: ["8000:8000"]
    env_file: [.env]                       # provider API keys (TAVILY_API_KEY, …)
    environment:
      OMNIFETCH_TRANSPORT: http
      OMNIFETCH_REST_FETCH: "true"         # the REST convenience surface (#5)
      OMNIFETCH_CACHE_BACKEND: ${OMNIFETCH_CACHE_BACKEND:-memory}
      OMNIFETCH_LOG_LEVEL: ${OMNIFETCH_LOG_LEVEL:-INFO}
      # opt-in tracing → any OTLP backend (cloud-agnostic, doc 12):
      OTEL_TRACES_EXPORTER: ${OTEL_TRACES_EXPORTER:-}
      OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-}
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  redis:                                   # `docker compose --profile redis up`
    image: redis:7-alpine
    profiles: ["redis"]
    ports: ["6379:6379"]
    restart: unless-stopped
```
To use Redis: `OMNIFETCH_CACHE_BACKEND=redis OMNIFETCH_REDIS_URL=redis://redis:6379/0
docker compose --profile redis up` (and add `depends_on: [redis]` under that
profile). With no profile, the in-memory `MemoryStore` default needs no second
container — the smallest possible footprint.

**Liveness `GET /health` (small addition).** Add a trivial, always-on
`@server.custom_route("/health", methods=["GET"])` returning
`{"status": "ok", "providers": <active_count>}` (mirrors omnisearch
`worker.ts:419-435`) so orchestrators (compose, k8s probes, ECS) have a vendor-
neutral health/readiness endpoint. It's ~5 lines and lives next to the REST
`/fetch` route in `server.py` (gate it on the HTTP transport like `/fetch`).

---

## 15.5 Usage (README snippet)
```bash
# build
docker build -t omnifetch:local .

# run as an HTTP server (REST /fetch + streamable MCP on :8000)
docker compose up -d
curl -s localhost:8000/fetch -H 'content-type: application/json' \
     -d '{"url":"https://example.com"}' | jq

# run as a stdio MCP server (a client attaches to stdin/stdout)
docker run --rm -i --env-file .env omnifetch:local

# with a shared Redis cache
OMNIFETCH_CACHE_BACKEND=redis OMNIFETCH_REDIS_URL=redis://redis:6379/0 \
  docker compose --profile redis up -d
```
MCP client registration over stdio (Docker):
```json
{ "mcpServers": { "omnifetch": {
  "command": "docker", "args": ["run","--rm","-i","--env-file",".env","omnifetch:local"] } } }
```

---

## 15.6 Acceptance criteria
1. `docker build .` succeeds; the runtime image runs as non-root and `omnifetch
   --help` works inside it.
2. `docker compose up -d` serves HTTP on `:8000`; `GET /health` returns
   `{"status":"ok",…}` and the compose healthcheck goes healthy.
3. `POST /fetch {"url": …}` against the running container returns a flattened
   `FetchResponse` (with provider keys set; mockable in CI via a stub upstream).
4. `OMNIFETCH_REST_FETCH=false` → `/fetch` is 404 but `/health` + MCP still serve.
5. `--profile redis` brings up Redis and the app uses `cache_backend=redis`
   (assert a warm hit survives an app-container restart; the memory default does
   not — proves the backend switch).
6. `docker run -i` (no command) serves **stdio** MCP (an in-process test client or
   `mcp` CLI completes `initialize` + `list_tools` showing `fetch`).
7. Image contains **no** secrets (keys arrive only via `--env-file`/compose env);
   `.dockerignore` excludes `plan/`, `tests/`, scratch dirs.
8. Works under both Docker and Podman (rootless) — no daemon-specific assumptions.

## 15.7 Interfaces
**Adds (repo root):** `Dockerfile`, `.dockerignore`, `docker-compose.yml`, and a
small always-on `GET /health` route in `server.py`. **Consumes:** the built
`omnifetch` console script, `ServerSettings` (`OMNIFETCH_TRANSPORT`,
`OMNIFETCH_REST_FETCH`, `OMNIFETCH_CACHE_BACKEND`, `OMNIFETCH_REDIS_URL`), and the
`performance` extra (uvloop). **No cloud-provider dependency.**
