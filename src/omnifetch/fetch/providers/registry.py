"""Provider auto-import and unified fetch dispatcher.

The dispatcher mirrors the old unified fetch provider contract: callers pass an
explicit provider name, and only providers whose declared secrets are configured
are active.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable, Mapping
from typing import cast

import httpx

from omnifetch.fetch.providers import base
from omnifetch.fetch.providers.base import FetchProvider
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_DEFAULT_PACKAGE_NAME = "omnifetch.fetch.providers"
_INFRA_MODULE_NAMES = frozenset({"base", "kimi_proxy", "registry"})


def import_all_providers(
    package_name: str = _DEFAULT_PACKAGE_NAME,
) -> Mapping[str, type[FetchProvider]]:
    """Import provider modules and return the live provider registry."""
    package = importlib.import_module(package_name)
    package_paths_object = getattr(package, "__path__", None)
    if package_paths_object is None:
        raise TypeError(f"{package_name!r} is not a package")

    package_paths = cast(Iterable[str], package_paths_object)
    for module_info in pkgutil.iter_modules(package_paths):
        if module_info.name not in _INFRA_MODULE_NAMES:
            importlib.import_module(f"{package_name}.{module_info.name}")
    return base.get_provider_classes()


def get_active_fetch_providers(
    secrets: ProviderSecrets,
    package_name: str = _DEFAULT_PACKAGE_NAME,
) -> list[str]:
    """Return provider names whose required secrets are configured."""
    return [
        name
        for name, provider_class in import_all_providers(package_name).items()
        if provider_class.is_available(secrets)
    ]


def has_any_fetch_provider(
    secrets: ProviderSecrets,
    package_name: str = _DEFAULT_PACKAGE_NAME,
) -> bool:
    """Return whether at least one fetch provider is available."""
    return any(
        provider_class.is_available(secrets)
        for provider_class in import_all_providers(package_name).values()
    )


class UnifiedFetchProvider:
    """Dispatch fetch requests to active providers by explicit name."""

    name = "fetch"

    def __init__(
        self,
        secrets: ProviderSecrets,
        client: httpx.AsyncClient,
        package_name: str = _DEFAULT_PACKAGE_NAME,
    ) -> None:
        """Initialize available provider instances from the registry."""
        provider_classes = import_all_providers(package_name)
        self._providers = {
            name: provider_class(secrets, client)
            for name, provider_class in provider_classes.items()
            if provider_class.is_available(secrets)
        }

    @property
    def active_names(self) -> list[str]:
        """Return active provider names in registration order."""
        return list(self._providers)

    @property
    def description(self) -> str:
        """Return a provider listing for tool metadata."""
        providers = ", ".join(self.active_names)
        return f"Fetch URL content as markdown. Providers: {providers}."

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        """Dispatch ``url`` to the selected active provider."""
        if provider is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                "Provider parameter is required for dispatch",
                self.name,
            )
        selected = self._providers.get(provider)
        if selected is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                f"Invalid provider: {provider}. "
                f"Valid: {', '.join(self._providers)}",
                self.name,
            )
        return await selected.fetch_url(url)
