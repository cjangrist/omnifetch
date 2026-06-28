"""Tests for fetch provider self-registration and dispatch."""

from __future__ import annotations

import abc
import importlib
from pathlib import Path

import httpx
import pytest

from omnifetch.fetch.providers import (
    base,
    FetchProvider,
    get_active_fetch_providers,
    get_provider_classes,
    has_any_fetch_provider,
    import_all_providers,
    UnifiedFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError


@pytest.fixture(autouse=True)
def _reset_provider_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test an empty provider registry."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.invalidate_caches()


def _result(provider: str, url: str = "https://example.test") -> FetchResult:
    """Return a valid fetch result for registry tests."""
    return FetchResult(
        url=url,
        title="Example",
        content="# Example\n\nContent",
        source_provider=provider,
    )


async def test_concrete_provider_self_registers() -> None:
    class AlphaProvider(FetchProvider):
        name = "alpha"
        description = "Alpha provider."
        base_url = "https://alpha.test"
        timeout_ms = 12_500
        required_secrets = ("ALPHA_API_KEY",)

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    assert get_provider_classes() == {"alpha": AlphaProvider}
    assert AlphaProvider.is_available(
        ProviderSecrets({"ALPHA_API_KEY": "secret"})
    )
    assert not AlphaProvider.is_available(ProviderSecrets({}))

    async with httpx.AsyncClient() as client:
        provider = AlphaProvider(ProviderSecrets({}), client)
        assert provider.timeout_s == 12.5


def test_abstract_provider_does_not_register() -> None:
    class AbstractProvider(FetchProvider):
        name = "abstract"

        @abc.abstractmethod
        async def fetch_url(self, url: str) -> FetchResult:
            raise NotImplementedError

    assert get_provider_classes() == {}


def test_empty_provider_name_raises_type_error() -> None:
    with pytest.raises(TypeError, match="non-empty provider name"):

        class EmptyNameProvider(FetchProvider):
            name = ""

            async def fetch_url(self, url: str) -> FetchResult:
                return _result(self.name, url)


def test_duplicate_provider_name_raises_type_error() -> None:
    class FirstProvider(FetchProvider):
        name = "duplicate"

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    with pytest.raises(TypeError, match="duplicate provider name"):

        class SecondProvider(FetchProvider):
            name = "duplicate"

            async def fetch_url(self, url: str) -> FetchResult:
                return _result(self.name, url)


def test_import_all_providers_imports_package_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "temporary_providers"
    package.mkdir()
    (package / "__init__.py").write_text(
        '"""Temporary provider package."""\n',
        encoding="utf-8",
    )
    (package / "registry.py").write_text(
        'raise RuntimeError("infra module should be skipped")\n',
        encoding="utf-8",
    )
    (package / "alpha.py").write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "from omnifetch.fetch.providers.base import FetchProvider",
                "from omnifetch.fetch.shared.types import FetchResult",
                "",
                "class AlphaProvider(FetchProvider):",
                '    name = "alpha"',
                '    required_secrets = ("ALPHA_API_KEY",)',
                "",
                "    async def fetch_url(self, url: str) -> FetchResult:",
                "        return FetchResult(",
                "            url=url,",
                '            title="Alpha",',
                '            content="# Alpha",',
                '            source_provider="alpha",',
                "        )",
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    assert list(import_all_providers("temporary_providers")) == ["alpha"]


def test_import_all_providers_rejects_plain_module() -> None:
    with pytest.raises(TypeError, match="is not a package"):
        import_all_providers("math")


async def test_unified_provider_dispatches_active_provider() -> None:
    class AlphaProvider(FetchProvider):
        name = "alpha"
        required_secrets = ("ALPHA_API_KEY",)

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    class BetaProvider(FetchProvider):
        name = "beta"
        required_secrets = ("BETA_API_KEY",)

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    async with httpx.AsyncClient() as client:
        unified = UnifiedFetchProvider(
            ProviderSecrets({"ALPHA_API_KEY": "secret"}),
            client,
        )
        result = await unified.fetch_url(
            "https://example.test/page",
            provider="alpha",
        )

    assert unified.active_names == ["alpha"]
    assert result == _result("alpha", "https://example.test/page")
    assert unified.description == (
        "Fetch URL content as markdown. Providers: alpha."
    )


async def test_unified_provider_requires_provider_name() -> None:
    async with httpx.AsyncClient() as client:
        unified = UnifiedFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await unified.fetch_url("https://example.test")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert (
        str(error_info.value) == "Provider parameter is required for dispatch"
    )
    assert error_info.value.provider == "fetch"


async def test_unified_provider_rejects_inactive_provider() -> None:
    class AlphaProvider(FetchProvider):
        name = "alpha"
        required_secrets = ("ALPHA_API_KEY",)

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    async with httpx.AsyncClient() as client:
        unified = UnifiedFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await unified.fetch_url("https://example.test", provider="alpha")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "Invalid provider: alpha. Valid: "
    assert error_info.value.provider == "fetch"


def test_active_provider_helpers_follow_required_secrets() -> None:
    class AlphaProvider(FetchProvider):
        name = "alpha"
        required_secrets = ("ALPHA_API_KEY",)

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    class BetaProvider(FetchProvider):
        name = "beta"
        required_secrets = ("BETA_API_KEY",)

        async def fetch_url(self, url: str) -> FetchResult:
            return _result(self.name, url)

    secrets = ProviderSecrets({"BETA_API_KEY": "secret"})

    assert get_active_fetch_providers(secrets) == ["beta"]
    assert has_any_fetch_provider(secrets) is True
    assert has_any_fetch_provider(ProviderSecrets({})) is False
