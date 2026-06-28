"""Abstract base and self-registration for fetch providers.

Every concrete provider declares its upstream metadata as class attributes and
implements ``fetch_url``. Importing a provider module registers the subclass by
name; the unified dispatcher then instantiates only providers whose declared
provider-owned secrets are available.
"""

from __future__ import annotations

import abc
import inspect
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

import httpx

from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import FetchResult

_REGISTRY: dict[str, type[FetchProvider]] = {}


class FetchProvider(abc.ABC):
    """Base class for one upstream URL-fetch provider."""

    name: ClassVar[str]
    description: ClassVar[str] = ""
    base_url: ClassVar[str] = ""
    timeout_ms: ClassVar[int] = 30_000
    required_secrets: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register concrete subclasses under their declared provider name."""
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return

        provider_name = getattr(cls, "name", "")
        if not isinstance(provider_name, str) or not provider_name.strip():
            raise TypeError(
                f"{cls.__name__} must set a non-empty provider name"
            )
        if provider_name in _REGISTRY:
            registered = _REGISTRY[provider_name].__name__
            raise TypeError(
                f"duplicate provider name {provider_name!r} "
                f"({cls.__name__} vs {registered})"
            )
        _REGISTRY[provider_name] = cls

    def __init__(
        self,
        secrets: ProviderSecrets,
        client: httpx.AsyncClient,
    ) -> None:
        """Initialize the provider with immutable secrets and shared client."""
        self._secrets = secrets
        self._client = client

    @property
    def timeout_s(self) -> float:
        """Return the provider deadline in seconds."""
        return self.timeout_ms / 1000

    @classmethod
    def is_available(cls, secrets: ProviderSecrets) -> bool:
        """Return whether every provider-owned required secret is present."""
        return secrets.require_all(*cls.required_secrets)

    @abc.abstractmethod
    async def fetch_url(self, url: str) -> FetchResult:
        """Fetch ``url`` and return normalized markdown content."""


def get_provider_classes() -> Mapping[str, type[FetchProvider]]:
    """Return registered provider classes keyed by provider name."""
    return MappingProxyType(_REGISTRY.copy())
