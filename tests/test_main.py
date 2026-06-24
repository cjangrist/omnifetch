"""Tests for the CLI entry point wiring (omnifetch.__main__)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest

from omnifetch import __main__
from omnifetch.config import AppConfig, load_config


def test_parse_args_defaults() -> None:
    args = __main__.parse_args([])
    assert args.transport is None
    assert args.host is None
    assert args.port is None
    assert args.log_level is None


def test_parse_args_overrides() -> None:
    args = __main__.parse_args(
        ["--transport", "http", "--host", "0.0.0.0", "--port", "9000"]
    )
    assert args.transport == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_collect_overrides_drops_unset() -> None:
    namespace = argparse.Namespace(
        transport="http", host=None, port=None, log_level="DEBUG"
    )
    overrides = __main__.collect_overrides(namespace)
    assert overrides == {"transport": "http", "log_level": "DEBUG"}


def test_run_server_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    server = MagicMock()
    monkeypatch.setattr("omnifetch.server.build_server", lambda *_: server)
    __main__.run_server(load_config(transport="stdio"))
    server.run.assert_called_once_with(transport="stdio")


def test_run_server_http(monkeypatch: pytest.MonkeyPatch) -> None:
    server = MagicMock()
    monkeypatch.setattr("omnifetch.server.build_server", lambda *_: server)
    config = load_config(transport="http", host="1.2.3.4", port=9001)
    __main__.run_server(config)
    server.run.assert_called_once_with(
        transport="http", host="1.2.3.4", port=9001
    )


def test_main_runs_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(__main__, "load_dotenv", lambda *_: None)
    monkeypatch.setattr(__main__, "configure_logging", lambda *_: None)
    monkeypatch.setattr(__main__, "configure_telemetry", lambda *_: None)
    monkeypatch.setattr(
        __main__, "run_server", lambda config: captured.update(config=config)
    )
    __main__.main(["--transport", "stdio", "--log-level", "WARNING"])
    config = captured["config"]
    assert isinstance(config, AppConfig)
    assert config.server.transport == "stdio"
    assert config.server.log_level == "WARNING"
