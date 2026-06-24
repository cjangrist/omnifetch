"""Shared pytest fixtures and hermetic environment isolation.

Provides the in-memory FastMCP client recommended by the FastMCP testing guide
(https://gofastmcp.com/servers/testing) — the server instance is passed directly
to ``Client`` with no subprocess or network. An autouse fixture also strips
ambient ``OMNIFETCH_``/``OTEL_`` variables so settings are deterministic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport

from omnifetch.server import build_server


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient OMNIFETCH_/OTEL_ variables for deterministic settings."""
    for name in list(os.environ):
        if name.startswith(("OMNIFETCH_", "OTEL_")):
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def restore_package_logger() -> Iterator[None]:
    """Snapshot/restore the package logger so logging tests stay isolated."""
    logger = logging.getLogger("omnifetch")
    handlers = logger.handlers[:]
    level = logger.level
    propagate = logger.propagate
    try:
        yield
    finally:
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.fixture
def mcp_server() -> FastMCP:
    """A freshly built FastMCP server instance."""
    return build_server()


@pytest.fixture
async def mcp_client(
    mcp_server: FastMCP,
) -> AsyncIterator[Client[FastMCPTransport]]:
    """In-memory FastMCP client connected directly to the server instance."""
    async with Client(FastMCPTransport(mcp_server)) as client:
        yield client
