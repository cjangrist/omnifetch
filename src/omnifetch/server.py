"""FastMCP server assembly.

Builds a configured ``FastMCP`` instance with strict input validation and
masked error details, then registers the toolset.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from importlib.metadata import version
from typing import Any, cast

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from omnifetch.config import AppConfig, load_config
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.providers.registry import UnifiedFetchProvider
from omnifetch.fetch.shared.types import ErrorType, ProviderError
from omnifetch.logging import get_logger
from omnifetch.tools import register_tools
from omnifetch.tools.fetch import execute_web_fetch

_LOGGER = get_logger("server")

_NAME = "omnifetch"
_VERSION = version("omnifetch")
_INSTRUCTIONS = (
    "Omnifetch MCP server. Exposes strictly-typed, JSON-Schema-enforced tools."
)
_HTTP_MAX_CONNECTIONS = 100
_HTTP_MAX_KEEPALIVE_CONNECTIONS = 40
_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404
_HTTP_RATE_LIMITED = 429
_HTTP_BAD_GATEWAY = 502
_MAX_FETCH_URL_LENGTH = 2000


def build_engine(config: AppConfig) -> Engine:
    """Build the shared fetch runtime for one FastMCP server instance."""
    limits = httpx.Limits(
        max_connections=_HTTP_MAX_CONNECTIONS,
        max_keepalive_connections=_HTTP_MAX_KEEPALIVE_CONNECTIONS,
    )
    client = httpx.AsyncClient(
        http2=True,
        follow_redirects=True,
        limits=limits,
    )
    return Engine(
        unified=UnifiedFetchProvider(config.providers, client),
        client=client,
    )


def _status_for_provider_error(error: ProviderError) -> int:
    """Map fetch provider errors onto REST status codes."""
    if error.error_type is ErrorType.INVALID_INPUT:
        return _HTTP_BAD_REQUEST
    if error.error_type is ErrorType.NOT_FOUND:
        return _HTTP_NOT_FOUND
    if error.error_type is ErrorType.RATE_LIMIT:
        return _HTTP_RATE_LIMITED
    return _HTTP_BAD_GATEWAY


async def _request_json_object(request: Request) -> dict[str, Any] | Response:
    """Read a JSON object request body or return a 400 response."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            {"error": "request body must be valid JSON"},
            status_code=_HTTP_BAD_REQUEST,
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            {"error": "request body must be a JSON object"},
            status_code=_HTTP_BAD_REQUEST,
        )
    return cast(dict[str, Any], payload)


def _fetch_url_from_payload(payload: dict[str, Any]) -> str | Response:
    """Return a valid REST fetch URL or a 400 response."""
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        return JSONResponse(
            {"error": "url is required"},
            status_code=_HTTP_BAD_REQUEST,
        )
    if len(url) > _MAX_FETCH_URL_LENGTH:
        return JSONResponse(
            {"error": "url must be 2000 characters or fewer"},
            status_code=_HTTP_BAD_REQUEST,
        )
    return url


def _provider_from_payload(payload: dict[str, Any]) -> str | None | Response:
    """Return an optional explicit provider or a 400 response."""
    provider = payload.get("provider")
    if provider is None:
        return None
    if not isinstance(provider, str) or not provider.strip():
        return JSONResponse(
            {"error": "provider must be a non-empty string"},
            status_code=_HTTP_BAD_REQUEST,
        )
    return provider


def register_http_routes(
    server: FastMCP,
    engine: Engine,
    *,
    rest_web_fetch_enabled: bool,
) -> None:
    """Register custom HTTP routes on the FastMCP server."""

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "providers": len(engine.unified.active_names),
            }
        )

    if not rest_web_fetch_enabled:
        return

    @server.custom_route(
        "/web_fetch", methods=["POST"], include_in_schema=False
    )
    async def rest_web_fetch(request: Request) -> Response:
        payload = await _request_json_object(request)
        if isinstance(payload, Response):
            return payload
        url = _fetch_url_from_payload(payload)
        if isinstance(url, Response):
            return url
        provider = _provider_from_payload(payload)
        if isinstance(provider, Response):
            return provider
        try:
            response = await execute_web_fetch(
                engine,
                url,
                provider=provider,
                skip_providers=payload.get("skip_providers"),
            )
        except ProviderError as error:
            return JSONResponse(
                {"error": str(error)},
                status_code=_status_for_provider_error(error),
            )
        return JSONResponse(response.model_dump(mode="json"))


def build_server(config: AppConfig | None = None) -> FastMCP:
    """Construct and return a fully-registered FastMCP server.

    Strict input validation and error-detail masking are always on — they are
    core guarantees of the server, not runtime-tunable settings.
    """
    app_config = load_config() if config is None else config
    engine = build_engine(app_config)

    @contextlib.asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.client.aclose()

    _LOGGER.info("Building server %r (version %s).", _NAME, _VERSION)
    server: FastMCP = FastMCP(
        name=_NAME,
        version=_VERSION,
        instructions=_INSTRUCTIONS,
        strict_input_validation=True,
        mask_error_details=True,
        lifespan=lifespan,
    )
    register_tools(server, engine)
    register_http_routes(
        server,
        engine,
        rest_web_fetch_enabled=app_config.server.rest_web_fetch,
    )
    _LOGGER.info("Server %r ready.", _NAME)
    return server
