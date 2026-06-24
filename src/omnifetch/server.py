"""FastMCP server assembly.

Builds a configured ``FastMCP`` instance — strict input validation on, internal
error details masked — and registers the toolset. Importing this module imports
FastMCP, so ``omnifetch.telemetry.configure_telemetry`` must run before this
module is imported (see ``omnifetch.__main__``).
"""

from __future__ import annotations

from fastmcp import FastMCP

from omnifetch.config import ServerSettings
from omnifetch.logging import get_logger
from omnifetch.tools import register_tools

_LOGGER = get_logger("server")


def build_server(settings: ServerSettings) -> FastMCP:
    """Construct and return a fully-registered FastMCP server."""
    _LOGGER.info(
        "Building server %r (version %s).", settings.name, settings.version
    )
    server: FastMCP = FastMCP(
        name=settings.name,
        version=settings.version,
        instructions=settings.instructions,
        strict_input_validation=settings.strict_input_validation,
        mask_error_details=settings.mask_error_details,
    )
    register_tools(server)
    _LOGGER.info("Server %r ready.", settings.name)
    return server
