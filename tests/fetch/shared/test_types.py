"""Tests for shared fetch result and provider error types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError


def test_fetch_result_round_trips_provider_shape() -> None:
    payload: dict[str, object] = {
        "url": "https://example.com",
        "title": "Example",
        "content": "# Example",
        "source_provider": "jina",
        "metadata": {"status_code": 200, "tokens": 12},
    }
    assert FetchResult.model_validate(payload).model_dump() == payload


def test_fetch_result_allows_absent_metadata() -> None:
    result = FetchResult(
        url="https://example.com",
        title="Example",
        content="# Example",
        source_provider="jina",
    )
    assert result.metadata is None


def test_fetch_result_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        FetchResult.model_validate(
            {
                "url": "https://example.com",
                "title": "Example",
                "content": "# Example",
                "source_provider": "jina",
                "unexpected": True,
            }
        )


def test_provider_error_exposes_expected_metadata() -> None:
    error = ProviderError(ErrorType.NOT_FOUND, "x", "github", {"path": "nope"})
    assert str(error) == "x"
    assert error.error_type is ErrorType.NOT_FOUND
    assert error.provider == "github"
    assert error.details == {"path": "nope"}


def test_error_type_serializes_as_string() -> None:
    assert ErrorType.API_ERROR.value == "API_ERROR"
    assert str(ErrorType.RATE_LIMIT) == "RATE_LIMIT"
