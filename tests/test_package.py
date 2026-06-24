"""Tests for the package public surface (lazy ``build_server`` export)."""

from __future__ import annotations

import pytest

import omnifetch
from omnifetch.server import build_server


def test_version_is_exposed() -> None:
    assert isinstance(omnifetch.__version__, str)
    assert omnifetch.__version__


def test_build_server_is_lazily_exposed() -> None:
    assert omnifetch.build_server is build_server


def test_unknown_attribute_raises() -> None:
    with pytest.raises(AttributeError):
        _ = omnifetch.does_not_exist
