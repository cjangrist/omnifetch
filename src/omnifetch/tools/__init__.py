"""Tool registry.

A single ``register_tools`` entry point wires every tool module into a FastMCP
server, keeping the server module agnostic of individual tools. New tools are
added by appending their registration function to ``_REGISTRARS``.
"""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from omnifetch.logging import get_logger
from omnifetch.tools.hello import register_hello_tool

_LOGGER = get_logger("tools")

_REGISTRARS: tuple[Callable[[FastMCP], None], ...] = (register_hello_tool,)


def register_tools(server: FastMCP) -> None:
    """Register every available tool on the given FastMCP server."""
    _LOGGER.debug("Registering %d tool module(s).", len(_REGISTRARS))
    for register in _REGISTRARS:
        register(server)
