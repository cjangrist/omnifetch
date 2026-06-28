"""Tests for shared fetch utility helpers."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from omnifetch.fetch.shared.types import ErrorType, ProviderError
from omnifetch.fetch.shared.util import (
    basic_auth,
    create_error_response,
    handle_provider_error,
    handle_rate_limit,
    hash_key,
    is_not_found_error_message,
    provider_timeout,
    retry_with_backoff,
    sanitize_for_log,
    timing_safe_equal,
    validate_api_key,
)


def test_validate_api_key_rejects_missing_key() -> None:
    with pytest.raises(ProviderError) as error_info:
        validate_api_key(None, "p")
    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for p"


@pytest.mark.parametrize(
    ("raw_key", "expected"),
    [('"abc"', "abc"), ("'abc'", "abc"), ("  k ", "k"), ("'a b'", "a b")],
)
def test_validate_api_key_normalizes_present_key(
    raw_key: str, expected: str
) -> None:
    assert validate_api_key(raw_key, "p") == expected


def test_hash_key_matches_sha256_vector() -> None:
    value = "https://x"
    expected = f"fetch:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
    assert hash_key("fetch:", value) == expected


@pytest.mark.parametrize(
    ("username", "password", "expected"),
    [("u", "p", "dTpw"), ("key", "", "a2V5Og==")],
)
def test_basic_auth_matches_btoa_vector(
    username: str, password: str, expected: str
) -> None:
    assert basic_auth(username, password) == expected


def test_timing_safe_equal_matches_equal_values() -> None:
    assert timing_safe_equal("abc", "abc") is True


def test_timing_safe_equal_rejects_different_values() -> None:
    assert timing_safe_equal("abc", "abd") is False


def test_timing_safe_equal_rejects_different_lengths() -> None:
    assert timing_safe_equal("abc", "abcd") is False


def test_sanitize_for_log_strips_controls_and_clamps() -> None:
    assert sanitize_for_log("a\x00b\nc" + ("x" * 250)) == "abc" + ("x" * 197)


@pytest.mark.parametrize(
    ("message", "target_url", "expected"),
    [
        ("Target URL returned 404", None, True),
        ("target URL was not found", None, True),
        ("HTTP status 404 for target URL", None, True),
        ("404 Not Found", None, True),
        ("The server returned a 404", None, True),
        ("Could not find the page you requested", None, True),
        ("Account not found for this API key", None, False),
        ("Engine not found", None, False),
        ("Invalid response: 404", None, False),
        ("Resource limit: 404 concurrent connections", None, False),
        ("upstream proxy returned 404", None, False),
        ("blocked by provider", None, False),
        (
            "Failed to scrape https://shop.test/products/widget-404: reset",
            "https://shop.test/products/widget-404",
            False,
        ),
        (
            "Page https://shop.test/products/widget-404 failed: reset",
            "https://shop.test/products/Widget-404",
            False,
        ),
        (
            "Page shop.test/products/widget-404 failed: reset",
            "https://shop.test/products/Widget-404",
            False,
        ),
        (
            "URL shop.test/products/widget-404 failed: reset",
            "https://shop.test/products/Widget-404",
            False,
        ),
        (
            "URL shop.test/products/widget-404?source=provider failed: reset",
            "https://shop.test/products/Widget-404?source=provider",
            False,
        ),
        (
            "Page http://[bad failed: reset",
            "http://[bad",
            False,
        ),
        ("Target URL returned 404", "", True),
        ("Target URL returned 404", "?source=provider", True),
    ],
)
def test_is_not_found_error_message(
    message: str,
    target_url: str | None,
    expected: bool,
) -> None:
    assert is_not_found_error_message(message, target_url) is expected


def test_handle_rate_limit_raises_without_reset() -> None:
    with pytest.raises(ProviderError) as error_info:
        handle_rate_limit("p")
    assert error_info.value.error_type is ErrorType.RATE_LIMIT
    assert str(error_info.value) == "Rate limit exceeded for p"
    assert error_info.value.details is None


def test_handle_rate_limit_raises_with_reset() -> None:
    with pytest.raises(ProviderError) as error_info:
        handle_rate_limit("p", "2026-06-28T00:00:00.000Z")
    assert error_info.value.error_type is ErrorType.RATE_LIMIT
    assert "Reset at 2026-06-28T00:00:00.000Z" in str(error_info.value)
    assert error_info.value.details == {
        "reset_time": "2026-06-28T00:00:00.000Z"
    }


def test_handle_provider_error_reraises_provider_error() -> None:
    original = ProviderError(ErrorType.NOT_FOUND, "missing", "github")
    with pytest.raises(ProviderError) as error_info:
        handle_provider_error(original, "ignored", "fetch")
    assert error_info.value is original


def test_handle_provider_error_wraps_exception() -> None:
    with pytest.raises(ProviderError) as error_info:
        handle_provider_error(ValueError("x"), "p", "fetch")
    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Failed to fetch: x"
    assert error_info.value.provider == "p"


def test_create_error_response_formats_provider_error() -> None:
    error = ProviderError(ErrorType.API_ERROR, "boom", "p")
    assert create_error_response(error) == {"error": "p error: boom"}


def test_create_error_response_formats_unexpected_error() -> None:
    assert create_error_response(ValueError("boom")) == {
        "error": "Unexpected error: boom"
    }


async def test_provider_timeout_raises_timeout_error() -> None:
    with pytest.raises(TimeoutError):
        async with provider_timeout(50):
            await asyncio.sleep(1)


async def test_provider_timeout_allows_fast_operation() -> None:
    async with provider_timeout(1000):
        await asyncio.sleep(0)


async def test_retry_with_backoff_returns_success_without_retry() -> None:
    attempts = 0

    async def succeed() -> str:
        nonlocal attempts
        attempts += 1
        return "ok"

    result = await retry_with_backoff(
        succeed, min_timeout_ms=1, max_timeout_ms=1
    )
    assert result == "ok"
    assert attempts == 1


async def test_retry_with_backoff_retries_transient_provider_error() -> None:
    attempts = 0

    async def eventually_succeed() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError(ErrorType.PROVIDER_ERROR, "temporary", "p")
        return "ok"

    result = await retry_with_backoff(
        eventually_succeed, min_timeout_ms=1, max_timeout_ms=1
    )
    assert result == "ok"
    assert attempts == 2


async def test_retry_with_backoff_does_not_retry_invalid_input() -> None:
    attempts = 0

    async def fail_invalid() -> str:
        nonlocal attempts
        attempts += 1
        raise ProviderError(ErrorType.INVALID_INPUT, "bad", "p")

    with pytest.raises(ProviderError):
        await retry_with_backoff(
            fail_invalid, min_timeout_ms=1, max_timeout_ms=1
        )
    assert attempts == 1


async def test_retry_with_backoff_exhausts_transient_errors() -> None:
    attempts = 0

    async def fail_transient() -> str:
        nonlocal attempts
        attempts += 1
        raise ProviderError(ErrorType.PROVIDER_ERROR, "temporary", "p")

    with pytest.raises(ProviderError):
        await retry_with_backoff(
            fail_transient,
            max_retries=1,
            min_timeout_ms=1,
            max_timeout_ms=1,
        )
    assert attempts == 2


async def test_retry_with_backoff_retries_plain_exceptions() -> None:
    attempts = 0

    async def eventually_succeed() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("temporary")
        return "ok"

    result = await retry_with_backoff(
        eventually_succeed, min_timeout_ms=1, max_timeout_ms=1
    )
    assert result == "ok"
    assert attempts == 2
