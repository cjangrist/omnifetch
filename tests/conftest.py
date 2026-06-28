"""Shared pytest fixtures and hermetic environment isolation.

Each test builds its own in-memory ``Client`` against the ``mcp_server``
fixture inside the test's event loop, per the FastMCP testing guide
(https://gofastmcp.com/servers/testing), so the client's task group never
spans separate fixture/test event loops. An autouse fixture also clears
ambient runtime and provider variables so settings stay deterministic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest
from fastmcp import FastMCP

from omnifetch.server import build_server

_PROVIDER_ENV_NAMES = (
    "TAVILY_API_KEY",
    "FIRECRAWL_API_KEY",
    "JINA_API_KEY",
    "YOU_API_KEY",
    "BRIGHT_DATA_API_KEY",
    "BRIGHT_DATA_ZONE",
    "LINKUP_API_KEY",
    "DIFFBOT_TOKEN",
    "SOCIAVAULT_API_KEY",
    "SPIDER_CLOUD_API_TOKEN",
    "SCRAPFLY_API_KEY",
    "SCRAPEGRAPHAI_API_KEY",
    "SCRAPE_DO_API_TOKEN",
    "SCRAPELESS_API_KEY",
    "OPENGRAPH_IO_API_KEY",
    "SCRAPINGBEE_API_KEY",
    "SCRAPERAPI_API_KEY",
    "ZYTE_API_KEY",
    "SCRAPINGANT_API_KEY",
    "OXYLABS_WEB_SCRAPER_USERNAME",
    "OXYLABS_WEB_SCRAPER_PASSWORD",
    "OLOSTEP_API_KEY",
    "DECODO_WEB_SCRAPING_API_KEY",
    "SCRAPPEY_API_KEY",
    "LEADMAGIC_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_EMAIL",
    "CLOUDFLARE_API_KEY",
    "SERPAPI_API_KEY",
    "SUPADATA_API_KEY",
    "GITHUB_API_KEY",
    "KIMI_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient runtime/provider variables for deterministic settings."""
    for name in list(os.environ):
        if (
            name.startswith(("OMNIFETCH_", "OTEL_"))
            or name in _PROVIDER_ENV_NAMES
        ):
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def restore_package_logger() -> Iterator[None]:
    """Snapshot/restore the package logger so logging tests stay isolated."""
    logger = logging.getLogger("omnifetch")
    handlers = logger.handlers[:]
    level = logger.level
    propagate = logger.propagate
    try:
        yield
    finally:
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


@pytest.fixture
def mcp_server() -> FastMCP:
    """A freshly built FastMCP server instance."""
    return build_server()
