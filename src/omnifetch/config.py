"""Typed, immutable application configuration.

Loads server and OpenTelemetry settings from the environment into validated,
frozen settings objects. The entry point first populates the environment
from any ``.env`` file via ``python-dotenv`` (see ``omnifetch.__main__``),
so this module reads only ``os.environ`` and never touches the filesystem.
Server settings carry the ``OMNIFETCH_`` prefix; telemetry settings use the
standard ``OTEL_`` names so the server speaks OpenTelemetry's native
configuration vocabulary. Configuration is loaded once at the entry point
via ``load_config`` and passed explicitly thereafter, never read from
globals inside business logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from omnifetch import __version__

TransportName = Literal["stdio", "http", "sse"]
OtelExporterName = Literal["", "none", "console", "otlp"]
OtelProtocolName = Literal["grpc", "http/protobuf"]


class ServerSettings(BaseSettings):
    """Runtime settings for the FastMCP server (env prefix ``OMNIFETCH_``)."""

    model_config = SettingsConfigDict(
        env_prefix="OMNIFETCH_",
        extra="ignore",
        frozen=True,
    )

    name: str = "omnifetch"
    version: str = __version__
    instructions: str = (
        "Omnifetch MCP server. "
        "Exposes strictly-typed, JSON-Schema-enforced tools."
    )
    transport: TransportName = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    strict_input_validation: bool = True
    mask_error_details: bool = True


class TelemetrySettings(BaseSettings):
    """OpenTelemetry settings read from the standard ``OTEL_`` variable names.

    An empty ``otel_traces_exporter`` keeps tracing a no-op; set it to
    ``console`` or ``otlp`` to activate the OpenTelemetry SDK.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
    )

    otel_sdk_disabled: bool = False
    otel_service_name: str = "omnifetch-mcp"
    otel_traces_exporter: OtelExporterName = ""
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_protocol: OtelProtocolName = "http/protobuf"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Aggregate, immutable configuration passed explicitly through the app."""

    server: ServerSettings
    telemetry: TelemetrySettings


def load_config(**server_overrides: Any) -> AppConfig:
    """Load and validate configuration from the environment.

    ``server_overrides`` (e.g. parsed CLI flags) take precedence over the
    environment for server settings. Telemetry always comes from the
    environment.
    """
    server = ServerSettings(**server_overrides)
    telemetry = TelemetrySettings()
    return AppConfig(server=server, telemetry=telemetry)
