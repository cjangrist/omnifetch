"""Omnifetch — a production-grade FastMCP server.

Public package surface. Deliberately import-light: it does NOT import FastMCP at
module load so that the OpenTelemetry SDK can be installed (see
``omnifetch.telemetry``) before FastMCP is first imported. ``build_server`` is
therefore exposed lazily via module ``__getattr__``.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

__version__ = "0.1.0"

__all__ = ["__version__", "build_server"]

if TYPE_CHECKING:
    from omnifetch.server import build_server


def __getattr__(name: str) -> Any:
    """Lazily expose ``build_server`` without importing FastMCP."""
    if name == "build_server":
        from omnifetch.server import build_server

        return build_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
