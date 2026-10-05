"""Real HTTP pool regression for a cancelled ``web_fetch`` tool call.

An MCP client that cancels a call makes the server cancel the handler's AnyIO
scope, and AnyIO re-cancels that task on every event-loop iteration until it
exits. A loopback server accepts each provider request and never answers, so
the cancellation lands while a parallel race tier has requests in flight.
Nothing in httpx or httpcore is patched: the test asserts the outcome -- the
shared pool still serves the next request -- so any cancellation path that
strands a connection fails it.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport

from omnifetch.cache import build_cache_backend
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.shared.types import FetchResult
from omnifetch.tools.fetch import execute_web_fetch, register_web_fetch_tool

_PARALLEL_TIER = ["linkup", "cloudflare_browser"]


async def _serve(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        headers = await reader.readuntil(b"\r\n\r\n")
        if b" /fast " in headers:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                b"Connection: close\r\n\r\nok"
            )
            await writer.drain()
            return
        await reader.read()
    finally:
        writer.close()


class _HangingDispatcher:
    """Dispatcher whose every provider request waits on a silent server."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url
        self.started = 0

    @property
    def active_names(self) -> list[str]:
        return _PARALLEL_TIER

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        self.started += 1
        await self._client.post(f"{self._base_url}/hang", json={"url": url})
        raise AssertionError("the loopback server never answers /hang")


async def _wait_until_started(dispatcher: _HangingDispatcher) -> None:
    async with asyncio.timeout(1):
        while dispatcher.started < len(_PARALLEL_TIER):
            await asyncio.sleep(0)
    await asyncio.sleep(0.05)


async def test_cancelled_tool_call_leaves_the_shared_pool_usable() -> None:
    listener = await asyncio.start_server(_serve, "127.0.0.1", 0)
    base_url = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    async with (
        listener,
        httpx.AsyncClient(
            limits=httpx.Limits(max_connections=len(_PARALLEL_TIER)),
            timeout=5,
        ) as http_client,
    ):
        dispatcher = _HangingDispatcher(http_client, base_url)
        engine = Engine(
            unified=dispatcher,
            client=http_client,
            cache=build_cache_backend(
                "memory", disk_path="", redis_url="", max_entries=10
            ),
        )
        server = FastMCP(name="cancellation-test")
        register_web_fetch_tool(server, engine)
        async with Client(FastMCPTransport(server)) as mcp_client:
            call = asyncio.create_task(
                mcp_client.call_tool(
                    "web_fetch", {"url": "https://example.test/article"}
                )
            )
            await _wait_until_started(dispatcher)
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
        response = await http_client.get(
            f"{base_url}/fast", timeout=httpx.Timeout(5, pool=1)
        )

    assert response.text == "ok"


class _AbsorbingDispatcher:
    """Dispatcher whose provider absorbs its cancellation and then finishes."""

    def __init__(self, *, fail: bool) -> None:
        self._fail = fail
        self.started = asyncio.Event()

    @property
    def active_names(self) -> list[str]:
        return ["tavily"]

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            current = asyncio.current_task()
            assert current is not None
            current.uncancel()
        if self._fail:
            raise RuntimeError("provider failed after absorbing")
        return FetchResult(
            url=url,
            title="Absorbed",
            content="# Absorbed\n\n" + ("useful content " * 30),
            source_provider="tavily",
        )


@pytest.mark.parametrize(
    ("fail", "outcome"), [(False, "returned"), (True, "ProviderError")]
)
async def test_abandoned_fetch_logs_how_it_finished(
    caplog: pytest.LogCaptureFixture,
    fail: bool,
    outcome: str,
) -> None:
    dispatcher = _AbsorbingDispatcher(fail=fail)
    async with httpx.AsyncClient() as http_client:
        engine = Engine(
            unified=dispatcher,
            client=http_client,
            cache=build_cache_backend(
                "memory", disk_path="", redis_url="", max_entries=10
            ),
        )
        with caplog.at_level(logging.INFO, logger="omnifetch.tools.fetch"):
            caller = asyncio.create_task(
                execute_web_fetch(engine, "https://example.test/article")
            )
            async with asyncio.timeout(1):
                await dispatcher.started.wait()
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
            async with asyncio.timeout(1):
                while "Abandoned web_fetch:example.test" not in caplog.text:
                    await asyncio.sleep(0)

    assert caller.cancelled()
    assert f"cancelled: {outcome}" in caplog.text
