"""Tests for shared fetch HTTP helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from omnifetch.fetch.shared.config import HttpSettings
from omnifetch.fetch.shared.http import (
    _HOST_SEMAPHORES,
    _MAX_RESPONSE_BYTES,
    _parse_message,
    _redact,
    http_json,
    http_raw,
    http_text,
)
from omnifetch.fetch.shared.types import ErrorType, ProviderError


class _Payload(BaseModel):
    """Typed response payload used by ``http_json`` tests."""

    value: int


class _OneChunkStream(httpx.AsyncByteStream):
    """Async byte stream that tracks whether it was consumed."""

    def __init__(self, chunk: bytes) -> None:
        self.chunk = chunk
        self.read_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read_count += 1
        yield self.chunk

    async def aclose(self) -> None:
        self.closed = True


class _ChunkStream(httpx.AsyncByteStream):
    """Async byte stream that yields fixed-size chunks with optional delay."""

    def __init__(
        self, chunk_size: int, chunk_count: int, delay_s: float = 0.0
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_count = chunk_count
        self.delay_s = delay_s
        self.read_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunk_count):
            self.read_count += 1
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield b"x" * self.chunk_size

    async def aclose(self) -> None:
        self.closed = True


def _mock_client(handler: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    """Return an async client backed by ``handler``."""
    return httpx.AsyncClient(transport=handler)


def test_redact_replaces_sensitive_query_parameters() -> None:
    redacted = _redact("https://x.test/path?api_key=SECRET&q=1&Token=NOPE")
    assert "SECRET" not in redacted
    assert "NOPE" not in redacted
    assert "api_key=%5BREDACTED%5D" in redacted
    assert "Token=%5BREDACTED%5D" in redacted
    assert "q=1" in redacted


def test_redact_truncates_invalid_urls() -> None:
    assert (
        _redact("http://[bad" + ("x" * 250))
        == ("http://[bad" + ("x" * 250))[:200]
    )


def test_parse_message_prefers_json_fields() -> None:
    assert _parse_message('{"message": "hello"}', "fallback") == "hello"
    assert _parse_message('{"error": "bad"}', "fallback") == "bad"
    assert _parse_message('{"detail": "details"}', "fallback") == "details"


def test_parse_message_uses_fallback_for_non_object_json() -> None:
    assert _parse_message('"hello"', "fallback") == "fallback"


def test_parse_message_uses_fallback_for_invalid_json() -> None:
    assert _parse_message("not json", "fallback") == "fallback"


def test_parse_message_uses_fallback_for_empty_body() -> None:
    assert _parse_message("", "fallback") == "fallback"


async def test_http_json_returns_plain_data_with_injected_client() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.test/data").respond(json={"value": 3})
        async with httpx.AsyncClient() as client:
            assert await http_json(
                client, "provider", "https://api.test/data"
            ) == {"value": 3}


async def test_http_json_validates_model() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.test/model").respond(json={"value": 7})
        async with httpx.AsyncClient() as client:
            result = await http_json(
                client, "provider", "https://api.test/model", model=_Payload
            )
    assert result.value == 7


async def test_http_json_rejects_invalid_json() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.test/bad-json").respond(text="nope")
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await http_json(client, "provider", "https://api.test/bad-json")
    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid JSON response from provider"


@pytest.mark.parametrize(
    ("status", "body", "expected_type", "expected_message"),
    [
        (401, "{}", ErrorType.API_ERROR, "Invalid API key"),
        (
            403,
            "{}",
            ErrorType.API_ERROR,
            "API key does not have access to this endpoint",
        ),
        (429, "{}", ErrorType.RATE_LIMIT, "Rate limit exceeded for provider"),
        (
            500,
            '{"message": "down"}',
            ErrorType.PROVIDER_ERROR,
            "provider API internal error (500): down",
        ),
        (
            418,
            '{"error": "short"}',
            ErrorType.API_ERROR,
            "provider error (418): short",
        ),
    ],
)
async def test_status_map_raises_provider_errors(
    status: int,
    body: str,
    expected_type: ErrorType,
    expected_message: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as error_info:
            await http_text(client, "provider", "https://api.test/status")
    assert error_info.value.error_type is expected_type
    assert str(error_info.value) == expected_message


async def test_expected_status_returns_raw_body_and_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing", request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        assert await http_raw(
            client,
            "provider",
            "https://api.test/missing",
            expected_statuses=(404,),
        ) == ("missing", 404)


async def test_transport_error_maps_to_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as error_info:
            await http_text(client, "provider", "https://api.test/error")
    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == "boom"


async def test_content_length_guard_rejects_before_reading_body() -> None:
    stream = _OneChunkStream(b"x")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(_MAX_RESPONSE_BYTES + 1)},
            stream=stream,
            request=request,
        )

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as error_info:
            await http_text(client, "provider", "https://api.test/large")
    assert error_info.value.error_type is ErrorType.API_ERROR
    assert "Response too large" in str(error_info.value)
    assert stream.read_count == 0
    assert stream.closed is True


async def test_invalid_content_length_uses_streaming_guard() -> None:
    stream = _OneChunkStream(b"ok")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "not-an-int"},
            stream=stream,
            request=request,
        )

    async with _mock_client(httpx.MockTransport(handler)) as client:
        assert (
            await http_text(client, "provider", "https://api.test/invalid")
            == "ok"
        )
    assert stream.read_count == 1
    assert stream.closed is True


async def test_streaming_guard_rejects_chunked_oversized_body() -> None:
    stream = _ChunkStream(chunk_size=1024 * 1024, chunk_count=6)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError) as error_info:
            await http_text(client, "provider", "https://api.test/chunked")
    assert error_info.value.error_type is ErrorType.API_ERROR
    assert (
        str(error_info.value)
        == f"Response too large (>{_MAX_RESPONSE_BYTES} bytes)"
    )
    assert stream.read_count == 6
    assert stream.closed is True


async def test_streaming_guard_rejects_lying_content_length() -> None:
    stream = _ChunkStream(chunk_size=1024 * 1024, chunk_count=6)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "10"},
            stream=stream,
            request=request,
        )

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            await http_text(client, "provider", "https://api.test/lying")
    assert stream.read_count == 6
    assert stream.closed is True


async def test_redacted_url_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", request=request)

    with caplog.at_level(logging.DEBUG, logger="omnifetch.fetch.http"):
        async with _mock_client(httpx.MockTransport(handler)) as client:
            assert (
                await http_text(
                    client,
                    "provider",
                    "https://api.test/data?api_key=SECRET&q=1",
                )
                == "ok"
            )
    messages = [record.getMessage() for record in caplog.records]
    assert any("%5BREDACTED%5D" in message for message in messages)
    assert not any("SECRET" in message for message in messages)


async def test_same_host_concurrency_is_limited() -> None:
    _HOST_SEMAPHORES.clear()
    in_flight = 0
    peak = 0
    settings = HttpSettings(limit_per_host=2)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, text="ok", request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        results = await asyncio.gather(
            *(
                http_text(
                    client,
                    "provider",
                    f"https://same.test/{index}",
                    http_settings=settings,
                )
                for index in range(10)
            )
        )
    assert results == ["ok"] * 10
    assert peak == 2


async def test_default_host_limit_is_used_without_settings() -> None:
    _HOST_SEMAPHORES.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok", request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        assert (
            await http_text(client, "provider", "https://api.test/default")
            == "ok"
        )
    assert ("api.test", 20) in _HOST_SEMAPHORES


async def test_different_hosts_are_not_throttled_together() -> None:
    _HOST_SEMAPHORES.clear()
    in_flight = 0
    peak = 0
    settings = HttpSettings(limit_per_host=1)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, text="ok", request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        results = await asyncio.gather(
            http_text(
                client,
                "provider",
                "https://one.test/a",
                http_settings=settings,
            ),
            http_text(
                client,
                "provider",
                "https://two.test/a",
                http_settings=settings,
            ),
        )
    assert tuple(results) == ("ok", "ok")
    assert peak == 2


async def test_default_retry_count_does_not_retry() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text='{"message": "down"}', request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            await http_text(client, "provider", "https://api.test/retry")
    assert attempts == 1


async def test_transient_retry_can_succeed() -> None:
    attempts = 0
    settings = HttpSettings(transient_retries=1)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                500, text='{"message": "down"}', request=request
            )
        return httpx.Response(200, text="ok", request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        assert (
            await http_text(
                client,
                "provider",
                "https://api.test/retry",
                http_settings=settings,
            )
            == "ok"
        )
    assert attempts == 2


async def test_transient_retry_exhausts_once() -> None:
    attempts = 0
    settings = HttpSettings(transient_retries=1)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, text='{"message": "down"}', request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            await http_text(
                client,
                "provider",
                "https://api.test/retry",
                http_settings=settings,
            )
    assert attempts == 2


@pytest.mark.parametrize("status", [401, 403, 429, 404])
async def test_non_transient_statuses_do_not_retry(
    status: int,
) -> None:
    attempts = 0
    settings = HttpSettings(transient_retries=1)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text='{"message": "no"}', request=request)

    async with _mock_client(httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError):
            await http_text(
                client,
                "provider",
                "https://api.test/no-retry",
                http_settings=settings,
            )
    assert attempts == 1


def test_no_raw_http_clients_in_fetch_package() -> None:
    root = Path("src/omnifetch/fetch")
    forbidden = ("httpx.AsyncClient(", "import requests", "urllib.request")
    offenders = [
        str(path)
        for path in root.rglob("*.py")
        if path.name != "http.py"
        for needle in forbidden
        if needle in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_runtime_environment_reads_outside_config_module() -> None:
    root = Path("src/omnifetch")
    offenders = [
        str(path)
        for path in root.rglob("*.py")
        if path != Path("src/omnifetch/fetch/shared/config.py")
        and "os.environ" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
