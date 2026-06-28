"""Tests for fetch-provider secrets and runtime settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omnifetch.config import load_config, ServerSettings
from omnifetch.fetch.shared.config import (
    PROVIDER_ENV_NAMES,
    ProviderSecrets,
)


def test_provider_env_names_match_settings_aliases() -> None:
    aliases = {
        str(field.validation_alias)
        for field in ProviderSecrets.model_fields.values()
    }
    assert set(PROVIDER_ENV_NAMES) == aliases
    assert len(PROVIDER_ENV_NAMES) == 32


def test_provider_secrets_default_to_unconfigured() -> None:
    secrets = ProviderSecrets()
    assert secrets.tavily_api_key is None
    assert secrets.oxylabs_username is None
    assert secrets.oxylabs_password is None
    assert secrets.bright_data_zone == "unblocker"


def test_provider_secrets_read_native_environment_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("OXYLABS_WEB_SCRAPER_USERNAME", "oxylabs-user")
    monkeypatch.setenv("OXYLABS_WEB_SCRAPER_PASSWORD", "oxylabs-pass")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cloudflare-account")
    monkeypatch.setenv("CLOUDFLARE_EMAIL", "ops@example.test")
    monkeypatch.setenv("CLOUDFLARE_API_KEY", "cloudflare-secret")
    monkeypatch.setenv("BRIGHT_DATA_ZONE", "custom-zone")

    secrets = ProviderSecrets()

    assert secrets.tavily_api_key == "tavily-secret"
    assert secrets.oxylabs_username == "oxylabs-user"
    assert secrets.oxylabs_password == "oxylabs-pass"
    assert secrets.cloudflare_account_id == "cloudflare-account"
    assert secrets.cloudflare_email == "ops@example.test"
    assert secrets.cloudflare_api_key == "cloudflare-secret"
    assert secrets.bright_data_zone == "custom-zone"


def test_provider_secrets_ignore_omnifetch_prefixed_provider_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIFETCH_TAVILY_API_KEY", "wrong-prefix")
    assert ProviderSecrets().tavily_api_key is None


def test_provider_secrets_are_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    secrets = ProviderSecrets()

    with pytest.raises(ValidationError, match="Instance is frozen"):
        secrets.tavily_api_key = "new-secret"


def test_server_settings_include_fetch_runtime_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIFETCH_CACHE_BACKEND", "disk")
    monkeypatch.setenv("OMNIFETCH_REDIS_URL", "redis://cache.example.test/0")
    monkeypatch.setenv("OMNIFETCH_DISK_CACHE_PATH", "/tmp/omnifetch-cache")
    monkeypatch.setenv("OMNIFETCH_HTTP_LIMIT_PER_HOST", "7")
    monkeypatch.setenv("OMNIFETCH_HTTP_TRANSIENT_RETRIES", "1")
    monkeypatch.setenv("OMNIFETCH_UVLOOP", "off")
    monkeypatch.setenv("OMNIFETCH_REST_FETCH", "false")

    settings = ServerSettings()

    assert settings.cache_backend == "disk"
    assert settings.redis_url == "redis://cache.example.test/0"
    assert settings.disk_cache_path == "/tmp/omnifetch-cache"
    assert settings.http_limit_per_host == 7
    assert settings.http_transient_retries == 1
    assert settings.uvloop == "off"
    assert settings.rest_fetch is False


def test_load_config_includes_provider_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JINA_API_KEY", "jina-secret")

    config = load_config(transport="http")

    assert config.server.transport == "http"
    assert config.providers.jina_api_key == "jina-secret"
