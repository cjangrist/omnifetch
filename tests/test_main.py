"""Tests for the CLI entry point wiring (omnifetch.__main__)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest
import uvloop

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


def test_install_uvloop_installs_policy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(uvloop, "install", lambda: calls.append("install"))

    assert __main__.install_uvloop("auto") is True

    assert calls == ["install"]


def test_install_uvloop_installs_policy_when_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(uvloop, "install", lambda: calls.append("install"))

    assert __main__.install_uvloop("on") is True

    assert calls == ["install"]


def test_install_uvloop_skips_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(uvloop, "install", lambda: calls.append("install"))

    assert __main__.install_uvloop("off") is False

    assert calls == []


def test_main_runs_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    events: list[str] = []

    def record_load_dotenv(*_: object) -> None:
        events.append("load_dotenv")

    def record_configure_logging(*_: object) -> None:
        events.append("configure_logging")

    def record_install_uvloop(mode: str) -> bool:
        events.append(f"install_uvloop:{mode}")
        return True

    def record_configure_telemetry(*_: object) -> None:
        events.append("configure_telemetry")

    def record_run_server(config: AppConfig) -> None:
        events.append("run_server")
        captured.update(config=config)

    monkeypatch.setattr(__main__, "load_dotenv", record_load_dotenv)
    monkeypatch.setattr(__main__, "configure_logging", record_configure_logging)
    monkeypatch.setattr(__main__, "install_uvloop", record_install_uvloop)
    monkeypatch.setattr(
        __main__, "configure_telemetry", record_configure_telemetry
    )
    monkeypatch.setattr(__main__, "run_server", record_run_server)
    __main__.main(["--transport", "stdio", "--log-level", "WARNING"])
    config = captured["config"]
    assert isinstance(config, AppConfig)
    assert config.server.transport == "stdio"
    assert config.server.log_level == "WARNING"
    assert events == [
        "load_dotenv",
        "configure_logging",
        "install_uvloop:auto",
        "configure_telemetry",
        "run_server",
    ]
