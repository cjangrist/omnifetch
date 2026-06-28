"""Core fetch data and provider-attributed error types.

``FetchResult`` is the normalized payload every provider returns.
``ProviderError`` carries an ``ErrorType`` for failover routing.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorType(enum.StrEnum):
    """Provider error categories used by the fetch waterfall."""

    API_ERROR = "API_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ProviderError(Exception):
    """Provider-attributed error with failover-routing metadata."""

    def __init__(
        self,
        error_type: ErrorType,
        message: str,
        provider: str,
        details: Any | None = None,
    ) -> None:
        """Initialize the error with category and provider context."""
        super().__init__(message)
        self.error_type = error_type
        self.provider = provider
        self.details = details


class FetchResult(BaseModel):
    """Normalized fetched-content payload from a successful provider call."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str
    content: str
    source_provider: str
    metadata: dict[str, Any] | None = None
