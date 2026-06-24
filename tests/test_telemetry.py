"""Tests for the telemetry stub: disabled by default, enabled via OTEL_."""

from __future__ import annotations

import sys
import types

import pytest
from opentelemetry import trace

from omnifetch.config import TelemetrySettings
from omnifetch.telemetry import _build_otlp_exporter, configure_telemetry


def test_disabled_when_exporter_empty() -> None:
    assert (
        configure_telemetry(TelemetrySettings(otel_traces_exporter="")) is False
    )


def test_disabled_when_exporter_none() -> None:
    assert (
        configure_telemetry(TelemetrySettings(otel_traces_exporter="none"))
        is False
    )


def test_disabled_when_sdk_flag_set() -> None:
    settings = TelemetrySettings(
        otel_traces_exporter="console", otel_sdk_disabled=True
    )
    assert configure_telemetry(settings) is False


@pytest.mark.parametrize("protocol", ["grpc", "http/protobuf"])
def test_build_otlp_exporter_returns_exporter(protocol: str) -> None:
    exporter = _build_otlp_exporter("http://localhost:4318", protocol)
    assert exporter.__class__.__name__ == "OTLPSpanExporter"


def test_console_exporter_installs_a_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        trace, "set_tracer_provider", lambda p: captured.update(provider=p)
    )
    settings = TelemetrySettings(otel_traces_exporter="console")
    assert configure_telemetry(settings) is True
    assert captured.get("provider") is not None


def test_otlp_exporter_installs_a_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        trace, "set_tracer_provider", lambda p: captured.update(provider=p)
    )
    settings = TelemetrySettings(
        otel_traces_exporter="otlp",
        otel_exporter_otlp_endpoint="http://localhost:4318",
    )
    assert configure_telemetry(settings) is True
    assert captured.get("provider") is not None


def test_missing_sdk_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    empty = types.ModuleType("opentelemetry.sdk.resources")
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.resources", empty)
    settings = TelemetrySettings(otel_traces_exporter="console")
    assert configure_telemetry(settings) is False
