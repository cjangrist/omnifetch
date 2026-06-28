"""Fetch runtime settings and provider-secret access.

Runtime knobs are typed settings. Provider secrets are a redacted frozen mapping
because each provider owns its upstream secret environment variable names.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class HttpSettings:
    """Explicit HTTP helper settings."""

    limit_per_host: int = 20
    transient_retries: int = 0


@dataclass(frozen=True, slots=True)
class ProviderSecrets:
    """Frozen provider-secret lookup with redacted representation."""

    _values: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Snapshot secret values into an immutable mapping."""
        object.__setattr__(
            self,
            "_values",
            MappingProxyType(
                {key: value for key, value in self._values.items() if value}
            ),
        )

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> ProviderSecrets:
        """Snapshot provider secrets from the given environment mapping."""
        return cls(os.environ if environ is None else environ)

    @property
    def values(self) -> Mapping[str, str]:
        """Return the immutable secret mapping."""
        return self._values

    def get(self, env_name: str, default: str | None = None) -> str | None:
        """Return a provider-owned secret value by exact environment name."""
        return self._values.get(env_name, default)

    def require_all(self, *env_names: str) -> bool:
        """Return whether every named provider secret is configured."""
        return all(self.get(env_name) for env_name in env_names)

    def __repr__(self) -> str:
        """Return a redacted debug representation."""
        return f"{self.__class__.__name__}(count={len(self._values)})"
