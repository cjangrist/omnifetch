"""Shared pytest fixtures and hermetic environment isolation.

Each test builds its own in-memory ``Client`` against the ``mcp_server``
fixture inside the test's event loop, per the FastMCP testing guide
(https://gofastmcp.com/servers/testing), so the client's task group never
spans separate fixture/test event loops. An autouse fixture also clears
ambient ``OMNIFETCH_``/``OTEL_`` variables so settings stay deterministic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest
from fastmcp import FastMCP

from omnifetch.fetch.shared.config import PROVIDER_ENV_NAMES
from omnifetch.server import build_server


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient runtime/provider variables for deterministic settings."""
    for name in list(os.environ):
        if (
            name.startswith(("OMNIFETCH_", "OTEL_"))
            or name in PROVIDER_ENV_NAMES
        ):
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
