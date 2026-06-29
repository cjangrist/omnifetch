"""Behavioral tests for the ``fetch`` tool via the in-memory client."""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx
import pytest
import respx
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

import omnifetch.server as server_module
import omnifetch.tools.fetch as fetch_module
from omnifetch.config import load_config
from omnifetch.fetch.engine.race import (
    AlternativeFetchResult,
    FetchRaceResult,
    ProviderAttemptFailure,
)
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError
from omnifetch.server import build_server
from omnifetch.tools.fetch import register_fetch_tool


class _FakeDispatcher:
    """In-memory dispatcher for tool-adapter tests."""

    def __init__(self, active_names: list[str]) -> None:
        self._active_names = active_names

    @property
    def active_names(self) -> list[str]:
        return self._active_names

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        raise AssertionError("run_fetch_race should be monkeypatched")


def _result(provider: str, content: str | None = None) -> FetchResult:
    """Return a valid fetch result for tool-adapter tests."""
    return FetchResult(
        url=f"https://canonical.example/{provider}",
        title=provider.title(),
        content=content or f"# {provider}\n\n" + ("useful content " * 30),
        source_provider=provider,
        metadata={"provider": provider},
    )


def _fake_tool_server(
    active_names: list[str],
) -> tuple[FastMCP, httpx.AsyncClient]:
    client = httpx.AsyncClient()
    engine = Engine(unified=_FakeDispatcher(active_names), client=client)
    server = FastMCP(
        name="test-fetch",
        strict_input_validation=True,
        mask_error_details=True,
    )
    register_fetch_tool(server, engine)
    return server, client


async def test_fetch_tool_metadata_is_registered(
    mcp_server: FastMCP,
) -> None:
    async with Client(FastMCPTransport(mcp_server)) as client:
        tools = await client.list_tools()

    tool = next(item for item in tools if item.name == "fetch")
    assert tool.title == "URL Fetch (multi-provider waterfall)"
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is True
    assert tool.annotations.destructiveHint is False


async def test_fetch_tool_fetches_with_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/extract").respond(
            json={
                "results": [
                    {
                        "url": "https://canonical.example/article",
                        "raw_content": "# Tavily\n\n"
                        + ("useful content " * 30),
                    }
                ],
                "failed_results": [],
            }
        )
        server = build_server(load_config())
        async with Client(FastMCPTransport(server)) as client:
            result = await client.call_tool(
                "fetch",
                {"url": "https://example.test/article"},
            )

    assert result.is_error is False
    assert result.data.source_provider == "tavily"
    assert result.data.url == "https://canonical.example/article"
    assert result.data.content
    assert result.data.providers_attempted == ["tavily"]
    assert result.data.providers_failed == []
    assert result.data.alternative_results is None


async def test_fetch_tool_skips_tavily_and_uses_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fire-secret")
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.firecrawl.dev/v2/scrape").respond(
            json={
                "success": True,
                "data": {
                    "markdown": "# Firecrawl\n\n" + ("useful content " * 30),
                    "metadata": {
                        "title": "Firecrawl",
                        "sourceURL": "https://canonical.example/fire",
                    },
                },
            }
        )
        server = build_server(load_config())
        async with Client(FastMCPTransport(server)) as client:
            result = await client.call_tool(
                "fetch",
                {
                    "url": "https://example.test/article",
                    "skip_providers": "tavily",
                },
            )

    assert result.data.source_provider == "firecrawl"
    assert result.data.url == "https://canonical.example/fire"
    assert result.data.providers_attempted == ["firecrawl"]
    assert result.data.alternative_results is None


def test_health_route_reports_active_provider_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    server = build_server(load_config(transport="http"))

    with TestClient(server.http_app(transport="http")) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "providers": 1}


def test_rest_fetch_returns_tavily_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/extract").respond(
            json={
                "results": [
                    {
                        "url": "https://canonical.example/article",
                        "raw_content": "# Tavily\n\n"
                        + ("useful content " * 30),
                    }
                ],
                "failed_results": [],
            }
        )
        server = build_server(load_config(transport="http"))
        with TestClient(server.http_app(transport="http")) as client:
            response = client.post(
                "/fetch",
                json={"url": "https://example.test/article"},
            )

    assert response.status_code == 200
    assert response.json()["source_provider"] == "tavily"
    assert response.json()["url"] == "https://canonical.example/article"
    assert response.json()["providers_attempted"] == ["tavily"]


def test_rest_fetch_uses_explicit_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fire-secret")
    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.firecrawl.dev/v2/scrape").respond(
            json={
                "success": True,
                "data": {"markdown": "# Firecrawl\n\n" + ("content " * 30)},
            }
        )
        server = build_server(load_config(transport="http"))
        with TestClient(server.http_app(transport="http")) as client:
            response = client.post(
                "/fetch",
                json={
                    "url": "https://example.test/article",
                    "provider": "firecrawl",
                },
            )

    assert response.status_code == 200
    assert response.json()["source_provider"] == "firecrawl"
    assert response.json()["providers_attempted"] == ["firecrawl"]


def test_rest_fetch_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    server = build_server(load_config(transport="http", rest_fetch=False))

    with TestClient(server.http_app(transport="http")) as client:
        health_response = client.get("/health")
        fetch_response = client.post(
            "/fetch",
            json={"url": "https://example.test/article"},
        )

    assert health_response.status_code == 200
    assert fetch_response.status_code == 404


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "url is required"),
        ({"url": ""}, "url is required"),
        ({"url": 12}, "url is required"),
        ({"url": "x" * 2001}, "url must be 2000 characters or fewer"),
        (
            {
                "url": "https://example.test/article",
                "provider": 12,
            },
            "provider must be a non-empty string",
        ),
        (
            {
                "url": "https://example.test/article",
                "provider": "",
            },
            "provider must be a non-empty string",
        ),
    ],
)
def test_rest_fetch_rejects_invalid_payload(
    payload: dict[str, object],
    message: str,
) -> None:
    server = build_server(load_config(transport="http"))

    with TestClient(server.http_app(transport="http")) as client:
        response = client.post("/fetch", json=payload)

    assert response.status_code == 400
    assert response.json() == {"error": message}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"not-json", "request body must be valid JSON"),
        (b"[]", "request body must be a JSON object"),
    ],
)
def test_rest_fetch_rejects_invalid_json_body(
    content: bytes,
    expected: str,
) -> None:
    server = build_server(load_config(transport="http"))

    with TestClient(server.http_app(transport="http")) as client:
        response = client.post(
            "/fetch",
            content=content,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json() == {"error": expected}


def test_rest_fetch_maps_unknown_skip_provider_to_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    server = build_server(load_config(transport="http"))

    with TestClient(server.http_app(transport="http")) as client:
        response = client.post(
            "/fetch",
            json={
                "url": "https://example.test/article",
                "skip_providers": "bogus",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"].startswith(
        "Unknown skip_providers names: bogus"
    )


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        (ErrorType.INVALID_INPUT, 400),
        (ErrorType.NOT_FOUND, 404),
        (ErrorType.RATE_LIMIT, 429),
        (ErrorType.API_ERROR, 502),
    ],
)
def test_provider_errors_map_to_rest_status_codes(
    error_type: ErrorType,
    expected_status: int,
) -> None:
    error = ProviderError(error_type, "provider failed", "fetch")

    assert server_module._status_for_provider_error(error) == expected_status


async def test_fetch_tool_rejects_unknown_skip_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    server = build_server(load_config())
    async with Client(FastMCPTransport(server)) as client:
        with pytest.raises(
            ToolError,
            match="Unknown skip_providers names: bogus",
        ):
            await client.call_tool(
                "fetch",
                {
                    "url": "https://example.test/article",
                    "skip_providers": "bogus",
                },
            )


async def test_fetch_tool_flattens_alternative_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_run_fetch_race(
        dispatcher: _FakeDispatcher,
        url: str,
        *,
        provider: str | None = None,
        skip_providers: Iterable[str] = (),
    ) -> FetchRaceResult:
        assert provider is None
        calls.append((url, tuple(skip_providers)))
        return FetchRaceResult(
            requested_url=url,
            total_duration_ms=7,
            provider_used="firecrawl",
            providers_attempted=("firecrawl", "kimi"),
            providers_failed=(
                ProviderAttemptFailure("tavily", "paywall page", 5),
            ),
            result=_result("firecrawl"),
            alternative_results=(
                AlternativeFetchResult("kimi", _result("kimi")),
            ),
        )

    monkeypatch.setattr(fetch_module, "run_fetch_race", fake_run_fetch_race)
    server, client = _fake_tool_server(["tavily", "firecrawl", "kimi"])
    try:
        async with Client(FastMCPTransport(server)) as mcp_client:
            result = await mcp_client.call_tool(
                "fetch",
                {
                    "url": "https://example.test/article",
                    "skip_providers": ["TAVILY"],
                },
            )
    finally:
        await client.aclose()

    assert calls == [("https://example.test/article", ("tavily",))]
    assert result.data.source_provider == "firecrawl"
    assert result.data.providers_attempted == ["firecrawl", "kimi"]
    assert result.data.providers_failed[0].provider == "tavily"
    assert result.data.providers_failed[0].error == "paywall page"
    assert result.data.alternative_results[0].source_provider == "kimi"


async def test_fetch_tool_surfaces_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_fetch_race(
        dispatcher: _FakeDispatcher,
        url: str,
        *,
        provider: str | None = None,
        skip_providers: Iterable[str] = (),
    ) -> FetchRaceResult:
        raise ProviderError(
            ErrorType.PROVIDER_ERROR,
            f"All providers failed for {url}. Tried: tavily",
            "waterfall",
        )

    monkeypatch.setattr(fetch_module, "run_fetch_race", fake_run_fetch_race)
    server, client = _fake_tool_server(["tavily"])
    try:
        async with Client(FastMCPTransport(server)) as mcp_client:
            with pytest.raises(ToolError, match="All providers failed"):
                await mcp_client.call_tool(
                    "fetch",
                    {"url": "https://example.test/article"},
                )
    finally:
        await client.aclose()


async def test_fetch_tool_logs_url_without_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_content = "# Secret\n\n" + ("do not log " * 30)

    async def fake_run_fetch_race(
        dispatcher: _FakeDispatcher,
        url: str,
        *,
        provider: str | None = None,
        skip_providers: Iterable[str] = (),
    ) -> FetchRaceResult:
        return FetchRaceResult(
            requested_url=url,
            total_duration_ms=3,
            provider_used="tavily",
            providers_attempted=("tavily",),
            providers_failed=(),
            result=_result("tavily", secret_content),
        )

    monkeypatch.setattr(fetch_module, "run_fetch_race", fake_run_fetch_race)
    server, client = _fake_tool_server(["tavily"])
    try:
        with caplog.at_level(logging.INFO, logger="omnifetch.tools.fetch"):
            async with Client(FastMCPTransport(server)) as mcp_client:
                await mcp_client.call_tool(
                    "fetch",
                    {"url": "https://example.test/article"},
                )
    finally:
        await client.aclose()

    messages = [record.getMessage() for record in caplog.records]
    assert any("fetch" in message for message in messages)
    assert any(
        "https://example.test/article" in message for message in messages
    )
    assert not any(secret_content in message for message in messages)


async def test_server_lifespan_closes_shared_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_client = httpx.AsyncClient()
    engine = Engine(unified=_FakeDispatcher(["tavily"]), client=shared_client)
    monkeypatch.setattr(server_module, "build_engine", lambda _: engine)
    server = server_module.build_server(load_config())

    try:
        async with Client(FastMCPTransport(server)) as client:
            await client.list_tools()
            assert shared_client.is_closed is False
        assert shared_client.is_closed is True
    finally:
        if not shared_client.is_closed:
            await shared_client.aclose()
