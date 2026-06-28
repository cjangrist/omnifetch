"""FastMCP server assembly.

Builds a configured ``FastMCP`` instance with strict input validation and
masked error details, then registers the toolset.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from importlib.metadata import version

import httpx
from fastmcp import FastMCP

from omnifetch.config import AppConfig, load_config
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.logging import get_logger
from omnifetch.tools import register_tools

_LOGGER = get_logger("server")

_NAME = "omnifetch"
_VERSION = version("omnifetch")
_INSTRUCTIONS = (
    "Omnifetch MCP server. Exposes strictly-typed, JSON-Schema-enforced tools."
)
_HTTP_MAX_CONNECTIONS = 100
_HTTP_MAX_KEEPALIVE_CONNECTIONS = 40


def build_engine(config: AppConfig) -> Engine:
    """Build the shared fetch runtime for one FastMCP server instance."""
    limits = httpx.Limits(
        max_connections=_HTTP_MAX_CONNECTIONS,
        max_keepalive_connections=_HTTP_MAX_KEEPALIVE_CONNECTIONS,
    )
    client = httpx.AsyncClient(
        http2=True,
        follow_redirects=True,
        limits=limits,
    )
    return Engine(
        unified=UnifiedFetchProvider(config.providers, client),
        client=client,
    )


def build_server(config: AppConfig | None = None) -> FastMCP:
    """Construct and return a fully-registered FastMCP server.

    Strict input validation and error-detail masking are always on — they are
    core guarantees of the server, not runtime-tunable settings.
    """
    app_config = load_config() if config is None else config
    engine = build_engine(app_config)

    @contextlib.asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.client.aclose()

    _LOGGER.info("Building server %r (version %s).", _NAME, _VERSION)
    server: FastMCP = FastMCP(
        name=_NAME,
        version=_VERSION,
        instructions=_INSTRUCTIONS,
        strict_input_validation=True,
        mask_error_details=True,
        lifespan=lifespan,
    )
    register_tools(server, engine)
    _LOGGER.info("Server %r ready.", _NAME)
    return server
