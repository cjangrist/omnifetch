"""Shared fetch primitives with no engine or provider dependencies."""

from __future__ import annotations

from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.html import (
    extract_html_title,
    extract_markdown_title,
)
from omnifetch.fetch.shared.http import http_json, http_raw, http_text
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.fetch.shared.util import (
    basic_auth,
    create_error_response,
    handle_provider_error,
    handle_rate_limit,
    hash_key,
    provider_timeout,
    retry_with_backoff,
    sanitize_for_log,
    timing_safe_equal,
    validate_api_key,
)

__all__ = [
    "ErrorType",
    "FetchResult",
    "ProviderError",
    "ProviderSecrets",
    "basic_auth",
    "create_error_response",
    "extract_html_title",
    "extract_markdown_title",
    "handle_provider_error",
    "handle_rate_limit",
    "hash_key",
    "http_json",
    "http_raw",
    "http_text",
    "provider_timeout",
    "retry_with_backoff",
    "sanitize_for_log",
    "timing_safe_equal",
    "validate_api_key",
]
