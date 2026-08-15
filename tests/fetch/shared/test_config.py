"""Tests for fetch-provider secrets and runtime settings."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from omnifetch.config import (
    DEFAULT_CACHE_MAX_ENTRIES,
    DEFAULT_DISK_CACHE_PATH,
    DEFAULT_FETCH_CACHE_TTL_SECONDS,
    load_config,
    ServerSettings,
    TelemetrySettings,
)
from omnifetch.fetch.shared.config import HttpSettings, ProviderSecrets

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_server_settings_have_explicit_environment_aliases() -> None:
    aliases = {
        str(field.validation_alias)
        for field in ServerSettings.model_fields.values()
    }
    assert aliases == {
        "OMNIFETCH_TRANSPORT",
        "OMNIFETCH_HOST",
        "OMNIFETCH_PORT",
        "OMNIFETCH_LOG_LEVEL",
        "OMNIFETCH_CACHE_BACKEND",
        "OMNIFETCH_REDIS_URL",
        "OMNIFETCH_DISK_CACHE_PATH",
        "OMNIFETCH_CACHE_MAX_ENTRIES",
        "OMNIFETCH_FETCH_CACHE_TTL_SECONDS",
        "OMNIFETCH_HTTP_LIMIT_PER_HOST",
        "OMNIFETCH_HTTP_TRANSIENT_RETRIES",
        "OMNIFETCH_UVLOOP",
        "OMNIFETCH_REST_WEB_FETCH",
    }


def test_telemetry_settings_have_explicit_environment_aliases() -> None:
    aliases = {
        str(field.validation_alias)
        for field in TelemetrySettings.model_fields.values()
    }
    assert aliases == {
        "OTEL_SDK_DISABLED",
        "OTEL_SERVICE_NAME",
        "OTEL_TRACES_EXPORTER",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
    }


def test_provider_secrets_are_generic_exact_name_lookup() -> None:
    secrets = ProviderSecrets(
        {
            "TAVILY_API_KEY": "tavily-secret",
            "BRIGHT_DATA_ZONE": "custom-zone",
            "EMPTY_SECRET": "",
        }
    )

    assert secrets.get("TAVILY_API_KEY") == "tavily-secret"
    assert secrets.get("BRIGHT_DATA_ZONE", "unblocker") == "custom-zone"
    assert secrets.get("MISSING_API_KEY") is None
    assert secrets.get("MISSING_ZONE", "unblocker") == "unblocker"
    assert "EMPTY_SECRET" not in secrets.values
    assert secrets.require_all("TAVILY_API_KEY", "BRIGHT_DATA_ZONE") is True
    assert secrets.require_all("TAVILY_API_KEY", "MISSING_API_KEY") is False


def test_provider_secrets_can_snapshot_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    assert ProviderSecrets.from_env().get("JINA_API_KEY") == "jina-secret"


def test_provider_secrets_are_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    secrets = ProviderSecrets.from_env()

    with pytest.raises(FrozenInstanceError):
        secrets.__setattr__("_values", {})
    with pytest.raises(TypeError):
        cast(Any, secrets.values)["TAVILY_API_KEY"] = "new-secret"


def test_provider_secrets_repr_redacts_values() -> None:
    secrets = ProviderSecrets({"TAVILY_API_KEY": "tavily-secret"})
    assert repr(secrets) == "ProviderSecrets(count=1)"
    assert "tavily-secret" not in repr(secrets)
    assert "TAVILY_API_KEY" not in repr(secrets)


def test_server_settings_include_fetch_runtime_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIFETCH_CACHE_BACKEND", "disk")
    monkeypatch.setenv("OMNIFETCH_REDIS_URL", "redis://cache.example.test/0")
    monkeypatch.setenv("OMNIFETCH_DISK_CACHE_PATH", "/tmp/omnifetch-cache")
    monkeypatch.setenv("OMNIFETCH_CACHE_MAX_ENTRIES", "1234")
    monkeypatch.setenv("OMNIFETCH_FETCH_CACHE_TTL_SECONDS", "43200")
    monkeypatch.setenv("OMNIFETCH_HTTP_LIMIT_PER_HOST", "7")
    monkeypatch.setenv("OMNIFETCH_HTTP_TRANSIENT_RETRIES", "1")
    monkeypatch.setenv("OMNIFETCH_UVLOOP", "off")
    monkeypatch.setenv("OMNIFETCH_REST_WEB_FETCH", "false")

    settings = ServerSettings()

    assert settings.cache_backend == "disk"
    assert settings.redis_url == "redis://cache.example.test/0"
    assert settings.disk_cache_path == "/tmp/omnifetch-cache"
    assert settings.cache_max_entries == 1234
    assert settings.fetch_cache_ttl_seconds == 43200
    assert settings.http_limit_per_host == 7
    assert settings.http_transient_retries == 1
    assert settings.uvloop == "off"
    assert settings.rest_web_fetch is False
    assert settings.http_settings() == HttpSettings(
        limit_per_host=7,
        transient_retries=1,
    )


def test_server_settings_hide_redis_credentials_from_repr() -> None:
    redis_url = "redis://owner:sensitive-password@cache.example.test/0"
    settings = ServerSettings(redis_url=redis_url)
    config = load_config(redis_url=redis_url)

    assert settings.redis_url == redis_url
    assert redis_url not in repr(settings)
    assert redis_url not in repr(config)
    assert "sensitive-password" not in repr(settings)
    assert "sensitive-password" not in repr(config)


def test_cache_constants_match_server_setting_defaults() -> None:
    settings = ServerSettings()

    assert settings.disk_cache_path == DEFAULT_DISK_CACHE_PATH
    assert settings.cache_max_entries == DEFAULT_CACHE_MAX_ENTRIES
    assert settings.fetch_cache_ttl_seconds == DEFAULT_FETCH_CACHE_TTL_SECONDS


def test_server_settings_ignore_unaliased_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRANSPORT", "http")
    monkeypatch.setenv("HTTP_LIMIT_PER_HOST", "7")

    settings = ServerSettings()

    assert settings.transport == "stdio"
    assert settings.http_limit_per_host == 20


def test_load_config_includes_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")

    config = load_config(transport="http")

    assert config.server.transport == "http"
    assert config.providers.get("JINA_API_KEY") == "jina-secret"


def test_setting_validation_still_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        ServerSettings(http_limit_per_host=0)


@pytest.mark.parametrize(
    "field_name",
    ["cache_max_entries", "fetch_cache_ttl_seconds"],
)
def test_cache_settings_must_be_positive(field_name: str) -> None:
    with pytest.raises(ValidationError):
        ServerSettings.model_validate({field_name: 0})


def test_env_example_contains_every_typed_setting() -> None:
    configured_names = {
        line.split("=", maxsplit=1)[0]
        for line in (_PROJECT_ROOT / ".env.example")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    typed_names = {
        str(field.validation_alias)
        for settings_type in (ServerSettings, TelemetrySettings)
        for field in settings_type.model_fields.values()
    }

    assert typed_names <= configured_names
