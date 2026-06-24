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

- **Strict I/O contracts** — tool inputs and outputs are validated against generated
  JSON Schemas (`strict_input_validation`, Pydantic output models).
- **Fully typed** — passes `mypy --strict`; enforces the
  [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) via
  `pylint` (Google `pylintrc`) + `ruff` (Google docstrings, 80 columns).
- **OpenTelemetry-ready** — tracing is a zero-overhead no-op until enabled with one
  environment variable.
- **Observable** — every tool call is logged with its arguments via
  [`logdecorator`](https://github.com/sighalt/logdecorator); logs go to stderr so the
  stdio transport stays clean.
- **Reproducible** — exact-pinned dependencies with a committed `uv.lock`; CI matrix on
  Python 3.11–3.13.

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
uv run python -m omnifetch
# or, after install, the console script:
uv run omnifetch
```

Run over streamable HTTP:

```bash
uv run omnifetch --transport http --host 0.0.0.0 --port 8000
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

## Configuration

Copy `.env.example` to `.env` (loaded via `python-dotenv`). Real environment variables
take precedence over `.env`.

| Variable | Default | Description |
|---|---|---|
| `OMNIFETCH_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `OMNIFETCH_HOST` | `127.0.0.1` | Bind host (http/sse) |
| `OMNIFETCH_PORT` | `8000` | Bind port (http/sse) |
| `OMNIFETCH_LOG_LEVEL` | `INFO` | Logging level |
| `OMNIFETCH_STRICT_INPUT_VALIDATION` | `true` | Reject inputs that violate the schema |
| `OMNIFETCH_MASK_ERROR_DETAILS` | `true` | Hide internal error details from clients |
| `OTEL_TRACES_EXPORTER` | _(empty)_ | `console` or `otlp` to **enable** tracing |
| `OTEL_SERVICE_NAME` | `omnifetch-mcp` | Service name in traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | _(empty)_ | OTLP collector endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | `http/protobuf` or `grpc` |
| `OTEL_SDK_DISABLED` | `false` | Force-disable the SDK |

### Telemetry

Telemetry is **off** by default (zero overhead). Enable it by setting an exporter — the
OpenTelemetry SDK is installed before FastMCP, per OpenTelemetry's contract:

```bash
# print spans to stderr (no collector needed)
OTEL_TRACES_EXPORTER=console uv run omnifetch

# export to a collector (requires the telemetry extra)
OTEL_TRACES_EXPORTER=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run omnifetch
```

## Development

```bash
uv run pre-commit install            # enable the git hook (ruff, pylint, mypy)
uv run pytest                        # tests + coverage gate
uv run pre-commit run --all-files    # the full Google-style + type gate
uv build                             # build sdist + wheel
```

CI (`.github/workflows/ci.yml`) runs the same pre-commit gate, the test matrix, a
`pip-audit` dependency scan, and the build.

## Commit convention

Commits and PR titles follow [Conventional Commits](https://www.conventionalcommits.org)
with an optional leading [gitmoji](https://gitmoji.dev):

    <gitmoji> <type>(<optional scope>): <short imperative summary>

Examples: `✨ feat: add a greeting tool`, `🐛 fix(server): reject empty names`,
`👷 ci: pin actions to commit SHAs`. Types: `feat` ✨, `fix` 🐛, `docs` 📝,
`refactor` ♻️, `perf` ⚡️, `test` ✅, `build` 📦, `ci` 👷, `chore` 🔧.

Enforced two ways: a local `commit-msg` pre-commit hook, and a CI check on the
**PR title** — which, because the repo squash-merges, becomes the lone commit on
`main`.

## Project layout

```
src/omnifetch/
  __main__.py    CLI entry point (dotenv -> config -> logging -> telemetry -> serve)
  config.py      typed settings (pydantic-settings)
  logging.py     colorized stderr logging
  telemetry.py   OpenTelemetry bootstrap (opt-in)
  schemas.py     tool I/O Pydantic models
  server.py      FastMCP assembly
  tools/         tool registry (one module per tool)
tests/           in-memory client tests
```

## Supply-chain security

Dependency and build integrity are taken seriously here — see [SECURITY.md](SECURITY.md)
for the full policy. Highlights:

- **Hash-verified, exact-pinned deps** — `uv.lock` carries hashes for every (transitive)
  package; CI installs with `UV_LOCKED=1`, so drift or a hash mismatch fails the build.
- **SHA-pinned GitHub Actions** — every action is pinned to an immutable commit SHA, so a
  hijacked tag cannot inject code; Dependabot keeps them current.
- **Deny-by-default CI egress** — `step-security/harden-runner` blocks all runner network
  egress except an explicit allowlist (GitHub, PyPI, Sigstore).
- **Least-privilege token** — `GITHUB_TOKEN` defaults to `permissions: {}`; jobs opt in.
- **Scanning** — `pip-audit` + `actions/dependency-review-action` (deps) and CodeQL (SAST).
- **Signed build provenance** — wheels are attested with [SLSA provenance](https://slsa.dev);
  verify with `gh attestation verify <wheel> --repo cjangrist/omnifetch`.
- **OpenSSF Scorecard** — supply-chain posture scored and published (badge above).

## License

[MIT](LICENSE) © CJ Angrist
