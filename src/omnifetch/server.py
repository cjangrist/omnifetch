"""FastMCP server assembly.

Builds a configured ``FastMCP`` instance with strict input validation and
masked error details, then registers the toolset.
"""

from __future__ import annotations

from importlib.metadata import version

from fastmcp import FastMCP

from omnifetch.logging import get_logger
from omnifetch.tools import register_tools

_LOGGER = get_logger("server")

_NAME = "omnifetch"
_VERSION = version("omnifetch")
_INSTRUCTIONS = (
    "Omnifetch MCP server. Exposes strictly-typed, JSON-Schema-enforced tools."
)


def build_server() -> FastMCP:
    """Construct and return a fully-registered FastMCP server.

    Strict input validation and error-detail masking are always on — they are
    core guarantees of the server, not runtime-tunable settings.
    """
    _LOGGER.info("Building server %r (version %s).", _NAME, _VERSION)
    server: FastMCP = FastMCP(
        name=_NAME,
        version=_VERSION,
        instructions=_INSTRUCTIONS,
        strict_input_validation=True,
        mask_error_details=True,
    )
    register_tools(server)
    _LOGGER.info("Server %r ready.", _NAME)
    return server
