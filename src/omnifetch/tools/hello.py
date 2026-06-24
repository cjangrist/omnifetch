"""The ``say_hello`` tool: a strictly-typed, schema-enforced greeting."""

from __future__ import annotations

import logging

from fastmcp import Context, FastMCP
from logdecorator.asyncio import async_log_on_end, async_log_on_start
from mcp.types import ToolAnnotations

from omnifetch.logging import get_logger
from omnifetch.schemas import GreetableName, HelloResponse

_LOGGER = get_logger("tools.hello")

_TOOL_NAME = "say_hello"
_TOOL_TITLE = "Say Hello"
_TOOL_DESCRIPTION = (
    "Return a friendly greeting for the given name as a structured JSON "
    "object. Defaults to greeting 'World'."
)
_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, idempotentHint=True, openWorldHint=False
)


@async_log_on_start(
    logging.INFO,
    "Tool call: {callable.__name__}(name={name!r})",
    logger=_LOGGER,
)
@async_log_on_end(
    logging.INFO,
    "Tool exit: {callable.__name__}",
    logger=_LOGGER,
)
async def say_hello(
    name: GreetableName = "World", ctx: Context | None = None
) -> HelloResponse:
    """Build a greeting for ``name`` and return it as a validated response."""
    if ctx is not None:
        await ctx.info(f"Greeting {name!r}.")
    return HelloResponse(message=f"Hello, {name}!")


def register_hello_tool(server: FastMCP) -> None:
    """Register the ``say_hello`` tool on the given FastMCP server."""
    server.tool(
        name=_TOOL_NAME,
        title=_TOOL_TITLE,
        description=_TOOL_DESCRIPTION,
        annotations=_TOOL_ANNOTATIONS,
    )(say_hello)
