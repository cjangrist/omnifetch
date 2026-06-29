"""Tool registry.

A single ``register_tools`` entry point wires every tool module into a FastMCP
server, keeping the server module agnostic of individual tools. New tools are
added by appending their registration function to ``_REGISTRARS``.
"""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from omnifetch.fetch.engine.runtime import Engine
from omnifetch.logging import get_logger
from omnifetch.tools.fetch import register_web_fetch_tool
from omnifetch.tools.hello import register_hello_tool

_LOGGER = get_logger("tools")


def _register_hello_tool(server: FastMCP, _engine: Engine) -> None:
    """Register the hello tool while ignoring fetch dependencies."""
    register_hello_tool(server)


_REGISTRARS: tuple[Callable[[FastMCP, Engine], None], ...] = (
    _register_hello_tool,
    register_web_fetch_tool,
)


def register_tools(server: FastMCP, engine: Engine) -> None:
    """Register every available tool on the given FastMCP server."""
    _LOGGER.debug("Registering %d tool module(s).", len(_REGISTRARS))
    for register in _REGISTRARS:
        register(server, engine)
