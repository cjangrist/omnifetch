# omnifetch

[![CI](https://github.com/cjangrist/omnifetch/actions/workflows/ci.yml/badge.svg)](https://github.com/cjangrist/omnifetch/actions/workflows/ci.yml)
[![CodeQL](https://github.com/cjangrist/omnifetch/actions/workflows/codeql.yml/badge.svg)](https://github.com/cjangrist/omnifetch/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/cjangrist/omnifetch/badge)](https://scorecard.dev/viewer/?uri=github.com/cjangrist/omnifetch)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Code style: Google](https://img.shields.io/badge/style-Google-4285F4)](https://google.github.io/styleguide/pyguide.html)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A production-grade [FastMCP](https://gofastmcp.com) server exposing a strictly-typed,
JSON-Schema-enforced toolset over the [Model Context Protocol](https://modelcontextprotocol.io).

## Features

- **Strict I/O contracts** — every tool's input and output is validated against a generated
  JSON Schema (`additionalProperties: false`, Pydantic output models).
- **Fully typed** — passes `mypy --strict`; the
  [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) is enforced
  by `ruff` (Google docstrings, naming, 80 columns).
- **Opt-in OpenTelemetry** — tracing is a zero-overhead no-op until one environment variable
  is set.
- **Observable** — every tool call is logged with its arguments via
  [`logdecorator`](https://github.com/sighalt/logdecorator); logs go to stderr so the
  stdio transport stays clean.
- **Reproducible & guarded** — exact-pinned deps with a committed `uv.lock`, a CI matrix on
  Python 3.11–3.13, and pre-commit + a protected `main` to keep extensions in line.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
uv sync --all-extras        # core + telemetry extra + dev tools
```

## Usage

Run over stdio (the default MCP transport):

```bash
uv run omnifetch                # or: uv run python -m omnifetch
```

Run over streamable HTTP:

```bash
uv run omnifetch --transport http --host 127.0.0.1 --port 8000
```

HTTP transport also exposes:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/web_fetch \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com"}'
```

Register with an MCP client (e.g. Claude Code / Claude Desktop):

```json
{
  "mcpServers": {
    "omnifetch": { "command": "uv", "args": ["run", "omnifetch"] }
  }
}
```

### Tool: `say_hello`

| | |
|---|---|
| Input | `name: str` (optional, 1–100 chars, default `"World"`) |
| Output | `{ "message": "Hello, <name>!" }` (schema-enforced) |
| Hints | `readOnlyHint`, `idempotentHint`, `openWorldHint=false` |

### Tool: `web_fetch`

| | |
|---|---|
| Input | `url: str` (required, 1–2000 chars), `skip_providers: str | list[str]` (optional) |
| Output | `{ "url", "title", "content", "source_provider", "total_duration_ms", "metadata", "providers_attempted", "providers_failed", "alternative_results" }` (schema-enforced) |
| Hints | `readOnlyHint`, `idempotentHint`, `openWorldHint` |
| Providers | Tavily and Firecrawl are callable when their provider-native secret is configured. |

## Configuration

Copy `.env.example` to `.env` (loaded via `python-dotenv`); real environment variables take
precedence.

| Variable | Default | Description |
|---|---|---|
| `OMNIFETCH_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `OMNIFETCH_HOST` | `127.0.0.1` | Bind host (http/sse) |
| `OMNIFETCH_PORT` | `8000` | Bind port (http/sse) |
| `OMNIFETCH_LOG_LEVEL` | `INFO` | Logging level |
| `OMNIFETCH_CACHE_BACKEND` | `memory` | Fetch cache backend: `memory`, `redis`, or `disk` |
| `OMNIFETCH_REDIS_URL` | _(empty)_ | Redis URL when `OMNIFETCH_CACHE_BACKEND=redis` |
| `OMNIFETCH_DISK_CACHE_PATH` | `.cache/omnifetch` | Disk cache path when `OMNIFETCH_CACHE_BACKEND=disk` |
| `OMNIFETCH_HTTP_LIMIT_PER_HOST` | `20` | Per-host async HTTP concurrency cap |
| `OMNIFETCH_HTTP_TRANSIENT_RETRIES` | `0` | Transient fetch HTTP retries before provider failover |
| `OMNIFETCH_UVLOOP` | `auto` | `auto`, `on`, or `off` runtime loop selection |
| `OMNIFETCH_REST_WEB_FETCH` | `true` | Enable the HTTP `/web_fetch` convenience route |
| `OTEL_TRACES_EXPORTER` | _(empty)_ | `console` or `otlp` to **enable** tracing |
| `OTEL_SERVICE_NAME` | `omnifetch-mcp` | Service name in traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(empty)_ | OTLP collector endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | `http/protobuf` or `grpc` |
| `OTEL_SDK_DISABLED` | `false` | Force-disable the SDK |

Fetch provider secrets use provider-native names with no `OMNIFETCH_` prefix.
Configure any subset; missing providers remain disabled.

| Variable | Default | Enables |
|---|---|---|
| `TAVILY_API_KEY` | _(empty)_ | Tavily fetch |
| `FIRECRAWL_API_KEY` | _(empty)_ | Firecrawl fetch |
| `JINA_API_KEY` | _(empty)_ | Jina Reader |
| `YOU_API_KEY` | _(empty)_ | You.com fetch |
| `BRIGHT_DATA_API_KEY` | _(empty)_ | Bright Data |
| `BRIGHT_DATA_ZONE` | `unblocker` | Bright Data zone |
| `LINKUP_API_KEY` | _(empty)_ | Linkup |
| `DIFFBOT_TOKEN` | _(empty)_ | Diffbot |
| `SOCIAVAULT_API_KEY` | _(empty)_ | SociaVault |
| `SPIDER_CLOUD_API_TOKEN` | _(empty)_ | Spider Cloud |
| `SCRAPFLY_API_KEY` | _(empty)_ | Scrapfly and Kimi proxying |
| `SCRAPEGRAPHAI_API_KEY` | _(empty)_ | ScrapeGraphAI |
| `SCRAPE_DO_API_TOKEN` | _(empty)_ | Scrape.do |
| `SCRAPELESS_API_KEY` | _(empty)_ | Scrapeless |
| `OPENGRAPH_IO_API_KEY` | _(empty)_ | OpenGraph.io |
| `SCRAPINGBEE_API_KEY` | _(empty)_ | ScrapingBee |
| `SCRAPERAPI_API_KEY` | _(empty)_ | ScraperAPI |
| `ZYTE_API_KEY` | _(empty)_ | Zyte |
| `SCRAPINGANT_API_KEY` | _(empty)_ | ScrapingAnt |
| `OXYLABS_WEB_SCRAPER_USERNAME` | _(empty)_ | Oxylabs username |
| `OXYLABS_WEB_SCRAPER_PASSWORD` | _(empty)_ | Oxylabs password |
| `OLOSTEP_API_KEY` | _(empty)_ | Olostep |
| `DECODO_WEB_SCRAPING_API_KEY` | _(empty)_ | Decodo pre-encoded Basic auth key |
| `SCRAPPEY_API_KEY` | _(empty)_ | Scrappey |
| `LEADMAGIC_API_KEY` | _(empty)_ | LeadMagic |
| `CLOUDFLARE_ACCOUNT_ID` | _(empty)_ | Cloudflare Browser Rendering account |
| `CLOUDFLARE_EMAIL` | _(empty)_ | Cloudflare Browser Rendering email |
| `CLOUDFLARE_API_KEY` | _(empty)_ | Cloudflare Browser Rendering key |
| `SERPAPI_API_KEY` | _(empty)_ | SerpAPI explicit fetch provider |
| `SUPADATA_API_KEY` | _(empty)_ | Supadata |
| `GITHUB_API_KEY` | _(empty)_ | GitHub fetch |
| `KIMI_API_KEY` | _(empty)_ | Kimi fetch |

### Telemetry

Off by default. Set an exporter to enable it — either exporter needs the `telemetry` extra
(`uv sync --extra telemetry`):

```bash
OTEL_TRACES_EXPORTER=console uv run omnifetch                                   # spans to stderr
OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run omnifetch
```

## Development

```bash
uv run pre-commit install            # enable the git hooks (ruff, mypy, secrets, commit format)
uv run pytest                        # tests + 100% coverage gate
uv run pre-commit run --all-files    # the full style + type gate
uv build                             # build sdist + wheel
```

CI runs the same gate, the test matrix, a `pip-audit` scan, and a provenance-signed build.

### Adding a tool

1. Add `src/omnifetch/tools/<name>.py` with an `async` function returning a Pydantic model,
   plus a `register_<name>_tool(server, engine)` helper — copy `tools/hello.py` for
   server-only tools or `tools/fetch.py` for tools that need runtime dependencies.
2. Append that registrar to `_REGISTRARS` in `tools/__init__.py`.
3. Add tests. `uv run pytest` enforces that every tool has an input and output schema.

## Commit convention

Commits and PR titles follow [Conventional Commits](https://www.conventionalcommits.org)
with an optional leading [gitmoji](https://gitmoji.dev):

    <gitmoji> <type>(<optional scope>): <short imperative summary>

Examples: `✨ feat: add a greeting tool`, `🐛 fix(server): reject empty names`. Types:
`feat` ✨, `fix` 🐛, `docs` 📝, `refactor` ♻️, `perf` ⚡️, `test` ✅, `build` 📦, `ci` 👷,
`chore` 🔧, `style`, `revert`. Enforced by a local `commit-msg` hook and a CI check on the
PR title (which becomes the squash commit on `main`).

## Project layout

```
src/omnifetch/
  __main__.py    CLI entry point (dotenv → config → logging → telemetry → serve)
  config.py      typed settings (pydantic-settings)
  logging.py     colorized stderr logging
  telemetry.py   opt-in OpenTelemetry bootstrap
  schemas.py     tool I/O Pydantic models
  server.py      FastMCP assembly
  tools/         tool registry (one module per tool)
tests/           in-memory client tests
```

## Security

Supply-chain hardening — SHA-pinned actions, deny-by-default CI egress, CodeQL, dependency
review, signed SLSA provenance, and secret scanning — is documented in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © CJ Angrist
