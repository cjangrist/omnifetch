"""Typed, immutable application configuration.

Server settings use the ``OMNIFETCH_`` prefix; telemetry uses the standard
``OTEL_`` names. ``load_config`` reads ``os.environ`` once and returns a frozen
``AppConfig`` that is passed explicitly through the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from omnifetch.fetch.shared.config import ProviderSecrets

TransportName = Literal["stdio", "http", "sse"]
OtelExporterName = Literal["", "none", "console", "otlp"]
OtelProtocolName = Literal["grpc", "http/protobuf"]
CacheBackendName = Literal["memory", "redis", "disk"]
UvloopModeName = Literal["auto", "off", "on"]


class ServerSettings(BaseSettings):
    """Runtime settings for the FastMCP server (env prefix ``OMNIFETCH_``)."""

    model_config = SettingsConfigDict(
        env_prefix="OMNIFETCH_",
        extra="ignore",
        frozen=True,
    )

    transport: TransportName = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    cache_backend: CacheBackendName = "memory"
    redis_url: str = ""
    disk_cache_path: str = ".cache/omnifetch"
    http_limit_per_host: int = Field(default=20, ge=1)
    http_transient_retries: int = Field(default=0, ge=0)
    uvloop: UvloopModeName = "auto"
    rest_fetch: bool = True


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
    """Frozen aggregate of all settings, passed explicitly through the app."""

    server: ServerSettings
    telemetry: TelemetrySettings
    providers: ProviderSecrets


def load_config(**server_overrides: Any) -> AppConfig:
    """Read configuration from the environment into a frozen ``AppConfig``.

    ``server_overrides`` (e.g. parsed CLI flags) take precedence over the
    environment for server settings.
    """
    return AppConfig(
        server=ServerSettings(**server_overrides),
        telemetry=TelemetrySettings(),
        providers=ProviderSecrets(),
    )
