"""Typed, immutable application configuration.

Each runtime setting declares its exact environment variable via
``validation_alias``. ``load_config`` reads settings once and returns a frozen
``AppConfig`` that is passed explicitly through the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from omnifetch.fetch.shared.config import HttpSettings, ProviderSecrets

TransportName = Literal["stdio", "http", "sse"]
OtelExporterName = Literal["", "none", "console", "otlp"]
OtelProtocolName = Literal["grpc", "http/protobuf"]
CacheBackendName = Literal["memory", "redis", "disk"]
UvloopModeName = Literal["auto", "off", "on"]


class ServerSettings(BaseSettings):
    """Runtime settings for the FastMCP server."""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    transport: TransportName = Field(
        default="stdio", validation_alias="OMNIFETCH_TRANSPORT"
    )
    host: str = Field(default="127.0.0.1", validation_alias="OMNIFETCH_HOST")
    port: int = Field(
        default=8000, ge=1, le=65535, validation_alias="OMNIFETCH_PORT"
    )
    log_level: str = Field(
        default="INFO", validation_alias="OMNIFETCH_LOG_LEVEL"
    )
    cache_backend: CacheBackendName = Field(
        default="memory", validation_alias="OMNIFETCH_CACHE_BACKEND"
    )
    redis_url: str = Field(default="", validation_alias="OMNIFETCH_REDIS_URL")
    disk_cache_path: str = Field(
        default=".cache/omnifetch",
        validation_alias="OMNIFETCH_DISK_CACHE_PATH",
    )
    http_limit_per_host: int = Field(
        default=20, ge=1, validation_alias="OMNIFETCH_HTTP_LIMIT_PER_HOST"
    )
    http_transient_retries: int = Field(
        default=0,
        ge=0,
        validation_alias="OMNIFETCH_HTTP_TRANSIENT_RETRIES",
    )
    uvloop: UvloopModeName = Field(
        default="auto", validation_alias="OMNIFETCH_UVLOOP"
    )
    rest_fetch: bool = Field(
        default=True, validation_alias="OMNIFETCH_REST_FETCH"
    )

    def http_settings(self) -> HttpSettings:
        """Return explicit settings for shared HTTP helpers."""
        return HttpSettings(
            limit_per_host=self.http_limit_per_host,
            transient_retries=self.http_transient_retries,
        )


class TelemetrySettings(BaseSettings):
    """OpenTelemetry settings read from the standard ``OTEL_`` variable names.

    An empty ``otel_traces_exporter`` keeps tracing a no-op; set it to
    ``console`` or ``otlp`` to activate the OpenTelemetry SDK.
    """

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    otel_sdk_disabled: bool = Field(
        default=False, validation_alias="OTEL_SDK_DISABLED"
    )
    otel_service_name: str = Field(
        default="omnifetch-mcp", validation_alias="OTEL_SERVICE_NAME"
    )
    otel_traces_exporter: OtelExporterName = Field(
        default="", validation_alias="OTEL_TRACES_EXPORTER"
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    otel_exporter_otlp_protocol: OtelProtocolName = Field(
        default="http/protobuf",
        validation_alias="OTEL_EXPORTER_OTLP_PROTOCOL",
    )


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
        providers=ProviderSecrets.from_env(),
    )
