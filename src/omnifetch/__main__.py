"""Command-line entry point for the Omnifetch MCP server.

Loads any ``.env`` file via python-dotenv, parses CLI overrides, builds typed
configuration, configures colorized logging and (optionally) telemetry, then
builds and runs the FastMCP server. Critically, telemetry is configured *before*
the server module is imported, so the OpenTelemetry SDK is installed before
FastMCP initializes its tracer.

Run with:  python -m omnifetch  (or the installed ``omnifetch`` console script).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from dotenv import load_dotenv

from omnifetch.config import AppConfig, load_config
from omnifetch.logging import configure_logging, get_logger
from omnifetch.telemetry import configure_telemetry

_LOGGER = get_logger("main")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line overrides for server settings."""
    parser = argparse.ArgumentParser(
        prog="omnifetch", description="Run the Omnifetch FastMCP server."
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http", "sse"),
        default=None,
        help="Transport to serve on (default: OMNIFETCH_TRANSPORT or 'stdio').",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Bind host for http/sse transports "
            "(default: OMNIFETCH_HOST or 127.0.0.1)."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "Bind port for http/sse transports "
            "(default: OMNIFETCH_PORT or 8000)."
        ),
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=None,
        help=(
            "Logging level e.g. DEBUG/INFO/WARNING "
            "(default: OMNIFETCH_LOG_LEVEL or INFO)."
        ),
    )
    return parser.parse_args(argv)


def collect_overrides(args: argparse.Namespace) -> dict[str, object]:
    """Collect the CLI flags that were explicitly provided as overrides."""
    candidates = {
        "transport": args.transport,
        "host": args.host,
        "port": args.port,
        "log_level": args.log_level,
    }
    return {
        key: value for key, value in candidates.items() if value is not None
    }


def run_server(config: AppConfig) -> None:
    """Build and run the FastMCP server for the given configuration."""
    from omnifetch.server import build_server

    server = build_server(config.server)
    transport = config.server.transport
    _LOGGER.info("Starting server on transport %r.", transport)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport=transport,
            host=config.server.host,
            port=config.server.port,
        )


def main(argv: Sequence[str] | None = None) -> None:
    """Load ``.env`` via dotenv, configure the runtime, and start serving."""
    load_dotenv()
    args = parse_args(argv)
    config = load_config(**collect_overrides(args))
    configure_logging(config.server.log_level)
    _LOGGER.debug("Configuration loaded: %r.", config)
    configure_telemetry(config.telemetry)
    run_server(config)


if __name__ == "__main__":
    main()
