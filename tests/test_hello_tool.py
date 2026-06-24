"""Behavioral tests for the ``say_hello`` tool via the in-memory client."""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError

from omnifetch.schemas import HelloResponse
from omnifetch.tools.hello import say_hello


async def test_default_greeting_is_hello_world(
    mcp_client: Client[FastMCPTransport],
) -> None:
    result = await mcp_client.call_tool("say_hello", {})
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
    mcp_client: Client[FastMCPTransport], name: str, expected: str
) -> None:
    result = await mcp_client.call_tool("say_hello", {"name": name})
    assert result.data.message == expected


async def test_exactly_one_tool_registered(
    mcp_client: Client[FastMCPTransport],
) -> None:
    tools = await mcp_client.list_tools()
    assert [tool.name for tool in tools] == ["say_hello"]


async def test_tool_metadata_is_advertised(
    mcp_client: Client[FastMCPTransport],
) -> None:
    tool = (await mcp_client.list_tools())[0]
    assert tool.title == "Say Hello"
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True


async def test_pure_function_returns_validated_response() -> None:
    response = await say_hello()
    assert isinstance(response, HelloResponse)
    assert response.message == "Hello, World!"


async def test_invalid_input_raises_tool_error(
    mcp_client: Client[FastMCPTransport],
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("say_hello", {"name": "x" * 101})


async def test_tool_call_and_exit_are_logged_without_result(
    mcp_client: Client[FastMCPTransport], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="omnifetch.tools.hello"):
        await mcp_client.call_tool("say_hello", {"name": "Ada"})
    messages = [record.getMessage() for record in caplog.records]
    assert any("say_hello" in m and "Ada" in m for m in messages)
    assert "Tool exit: say_hello" in messages
    assert not any("Hello, Ada!" in m for m in messages)
