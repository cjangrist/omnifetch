"""Behavioral tests for the ``say_hello`` tool via the in-memory client."""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError

from omnifetch.schemas import HelloResponse
from omnifetch.tools import _REGISTRARS
from omnifetch.tools.hello import say_hello


async def test_default_greeting_is_hello_world(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        result = await client.call_tool("say_hello", {})
    assert result.is_error is False
    assert result.data.message == "Hello, World!"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ada", "Hello, Ada!"),
        ("World", "Hello, World!"),
        ("Grace Hopper", "Hello, Grace Hopper!"),
    ],
)
async def test_named_greeting(
    mcp_server: FastMCP, name: str, expected: str
) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        result = await client.call_tool("say_hello", {"name": name})
    assert result.data.message == expected


async def test_say_hello_is_registered(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        tools = await client.list_tools()
    assert "say_hello" in [tool.name for tool in tools]


async def test_every_registrar_produces_a_tool(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        tools = await client.list_tools()
    assert len(tools) == len(_REGISTRARS)


async def test_tool_metadata_is_advertised(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        tool = (await client.list_tools())[0]
    assert tool.title == "Say Hello"
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True


async def test_pure_function_returns_validated_response() -> None:
    response = await say_hello()
    assert isinstance(response, HelloResponse)
    assert response.message == "Hello, World!"


async def test_invalid_input_raises_tool_error(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("say_hello", {"name": "x" * 101})


async def test_tool_call_and_exit_are_logged_without_result(
    mcp_server: FastMCP, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="omnifetch.tools.hello"):
        async with Client(FastMCPTransport(mcp_server)) as client:
            await client.call_tool("say_hello", {"name": "Ada"})
    messages = [record.getMessage() for record in caplog.records]
    assert any("say_hello" in m and "Ada" in m for m in messages)
    assert "Tool exit: say_hello" in messages
    assert not any("Hello, Ada!" in m for m in messages)
