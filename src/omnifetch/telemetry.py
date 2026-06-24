"""OpenTelemetry tracing bootstrap (stub — disabled by default).

FastMCP emits spans through the OpenTelemetry *API*, which is a no-op until an
SDK ``TracerProvider`` is installed. This module installs one — but only when
tracing is explicitly enabled via the standard ``OTEL_TRACES_EXPORTER``
variable. It imports nothing from FastMCP, so it can (and must) run before
FastMCP is first imported, satisfying OpenTelemetry's "configure the SDK
before instrumentation" contract. The heavy SDK imports are optional (the
``telemetry`` extra) and are performed lazily so the core server runs
without them.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from omnifetch.config import TelemetrySettings
from omnifetch.logging import get_logger

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter

_LOGGER = get_logger("telemetry")
_DISABLED_EXPORTERS = frozenset({"", "none"})


def _build_otlp_exporter(endpoint: str, protocol: str) -> SpanExporter:
    """Construct an OTLP span exporter for the configured wire protocol."""
    target_endpoint = endpoint or None
    if protocol == "grpc":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcSpanExporter,
        )

        return GrpcSpanExporter(endpoint=target_endpoint)
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as HttpSpanExporter,
    )

    return HttpSpanExporter(endpoint=target_endpoint)


def configure_telemetry(settings: TelemetrySettings) -> bool:
    """Install an OpenTelemetry SDK tracer provider when tracing is enabled.

    Returns ``True`` when tracing was activated, ``False`` when it remained a
    no-op (disabled by configuration, or the optional ``telemetry`` extra is not
    installed).
    """
    exporter_name = settings.otel_traces_exporter.lower()
    if settings.otel_sdk_disabled or exporter_name in _DISABLED_EXPORTERS:
        _LOGGER.info(
            "Telemetry disabled (OTEL_TRACES_EXPORTER=%r); tracing is a no-op.",
            settings.otel_traces_exporter,
        )
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError:
        _LOGGER.error(
            "Telemetry requested but the OpenTelemetry SDK is missing. "
            "Install the extra:  pip install 'omnifetch[telemetry]'."
        )
        return False

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    if exporter_name == "console":
        provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr))
        )
    else:
        exporter = _build_otlp_exporter(
            settings.otel_exporter_otlp_endpoint,
            settings.otel_exporter_otlp_protocol,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _LOGGER.info(
        "Telemetry enabled (exporter=%s, service=%s).",
        exporter_name,
        settings.otel_service_name,
    )
    return True
