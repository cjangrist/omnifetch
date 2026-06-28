"""Tests that input/output JSON Schemas are generated *and* enforced."""

from __future__ import annotations

from typing import cast

from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from mcp.types import Tool


async def _tool_by_name(mcp_server: FastMCP, name: str) -> Tool:
    async with Client(FastMCPTransport(mcp_server)) as client:
        tools = cast(list[Tool], await client.list_tools())
    return next(tool for tool in tools if tool.name == name)


async def test_input_schema_constraints(mcp_server: FastMCP) -> None:
    schema = (await _tool_by_name(mcp_server, "say_hello")).inputSchema
    assert schema["additionalProperties"] is False
    name = schema["properties"]["name"]
    assert name["type"] == "string"
    assert name["minLength"] == 1
    assert name["maxLength"] == 100
    assert "ctx" not in schema["properties"]


async def test_output_schema_present(mcp_server: FastMCP) -> None:
    schema = (await _tool_by_name(mcp_server, "say_hello")).outputSchema
    assert schema is not None
    assert schema["properties"]["message"]["type"] == "string"
    assert schema["required"] == ["message"]


async def test_structured_output_matches_schema(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        result = await client.call_tool("say_hello", {"name": "Ada"})
    assert result.data.message == "Hello, Ada!"


async def test_extra_argument_rejected(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        result = await client.call_tool(
            "say_hello",
            {"name": "Ada", "unexpected": True},
            raise_on_error=False,
        )
    assert result.is_error is True


async def test_empty_name_rejected(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        result = await client.call_tool(
            "say_hello", {"name": ""}, raise_on_error=False
        )
    assert result.is_error is True


async def test_oversized_name_rejected(mcp_server: FastMCP) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        result = await client.call_tool(
            "say_hello", {"name": "x" * 101}, raise_on_error=False
        )
    assert result.is_error is True


async def test_every_tool_enforces_input_and_output_schema(
    mcp_server: FastMCP,
) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        tools = await client.list_tools()
    assert tools, "no tools registered"
    for tool in tools:
        assert tool.inputSchema.get("additionalProperties") is False, tool.name
        assert tool.outputSchema is not None, tool.name
