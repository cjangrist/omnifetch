"""FastMCP server assembly.

Builds a configured ``FastMCP`` instance with strict input validation and
masked error details, then registers the toolset.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from importlib.metadata import version
from typing import Any, cast

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from omnifetch.cache import build_cache_backend, CacheBackend
from omnifetch.config import AppConfig, load_config
from omnifetch.fetch.engine.runtime import Engine, same_url
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
_ROLLBACK_TASKS: set[asyncio.Task[None]] = set()


async def _close_owned_resources(
    cache: CacheBackend,
    *,
    owns_cache: bool,
    client: httpx.AsyncClient | None,
    owns_client: bool,
) -> None:
    """Close partially assembled owned resources without skipping either."""
    try:
        if owns_cache:
            await cache.close()
    finally:
        if owns_client and client is not None:
            await client.aclose()


def _run_rollback(
    cleanup: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    """Run cleanup now or schedule it on the active resource-owning loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(cleanup())
        except BaseException as error:
            _LOGGER.warning(
                "Resource rollback failed (%s)",
                type(error).__name__,
            )
    else:
        task = loop.create_task(cleanup())
        _ROLLBACK_TASKS.add(task)
        task.add_done_callback(_rollback_finished)


def _rollback_finished(task: asyncio.Task[None]) -> None:
    """Retire a scheduled rollback task and observe any cleanup failure."""
    _ROLLBACK_TASKS.discard(task)
    try:
        task.result()
    except BaseException as error:
        _LOGGER.warning(
            "Resource rollback failed (%s)",
            type(error).__name__,
        )


def _rollback_assembly(
    cache: CacheBackend,
    *,
    owns_cache: bool,
    client: httpx.AsyncClient | None,
    owns_client: bool,
) -> None:
    """Close resources allocated before an engine could be assembled."""

    async def cleanup() -> None:
        await _close_owned_resources(
            cache,
            owns_cache=owns_cache,
            client=client,
            owns_client=owns_client,
        )

    _run_rollback(cleanup)


def build_engine(
    config: AppConfig,
    client: httpx.AsyncClient | None = None,
    cache: CacheBackend | None = None,
    canonicalize_cache_url: Callable[[str], str] = same_url,
) -> Engine:
    """Build the shared fetch runtime for one FastMCP server instance.

    When ``client`` is None a fresh pooled ``httpx.AsyncClient`` is constructed
    and adopted by the engine. When a ``client`` is supplied it is used as-is
    so a composing server can share a single connection pool across engines.
    Likewise, a supplied cache is borrowed unchanged; otherwise the configured
    cachelib backend is constructed before allocating the HTTP client.
    """
    owns_cache = cache is None
    owns_client = client is None
    resolved_cache = (
        build_cache_backend(
            config.server.cache_backend,
            disk_path=config.server.disk_cache_path,
            redis_url=config.server.redis_url,
            max_entries=config.server.cache_max_entries,
        )
        if cache is None
        else cache
    )
    resolved_client = client
    try:
        if resolved_client is None:
            limits = httpx.Limits(
                max_connections=_HTTP_MAX_CONNECTIONS,
                max_keepalive_connections=_HTTP_MAX_KEEPALIVE_CONNECTIONS,
            )
            resolved_client = httpx.AsyncClient(
                http2=True,
                follow_redirects=True,
                limits=limits,
            )
        unified = UnifiedFetchProvider(config.providers, resolved_client)
        return Engine(
            unified=unified,
            client=resolved_client,
            cache=resolved_cache,
            fetch_cache_ttl_seconds=config.server.fetch_cache_ttl_seconds,
            volatile_fetch_cache_ttl_seconds=(
                config.server.volatile_fetch_cache_ttl_seconds
            ),
            canonicalize_cache_url=canonicalize_cache_url,
            owns_client=owns_client,
            owns_cache=owns_cache,
        )
    except BaseException:
        _rollback_assembly(
            resolved_cache,
            owns_cache=owns_cache,
            client=resolved_client,
            owns_client=owns_client,
        )
        raise


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


def _provider_from_payload(payload: dict[str, Any]) -> str | Response | None:
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


def build_server(
    config: AppConfig | None = None,
    engine: Engine | None = None,
    *,
    own_engine: bool = True,
) -> FastMCP:
    """Construct and return a fully-registered FastMCP server.

    Strict input validation and error-detail masking are always on — they are
    core guarantees of the server, not runtime-tunable settings.

    When ``engine`` is None one is built from ``config`` and the server lifespan
    owns it. When an ``engine`` is supplied it is adopted as-is; ``own_engine``
    then controls whether the lifespan closes its cache and HTTP client. Set
    ``own_engine=False`` for server composition, where shared resources outlive
    the mounted server and must not be closed at unmount time. An engine built
    here is always owned — ``own_engine=False`` together with ``engine=None``
    would leak the constructed resources and is rejected.
    """
    app_config = load_config() if config is None else config
    if engine is None and not own_engine:
        raise ValueError(
            "own_engine=False requires an engine to be supplied; the server "
            "must own any engine it builds so its resources are closed on "
            "shutdown. Pass an engine or drop own_engine=False."
        )
    if engine is None:
        engine = build_engine(app_config)

    @contextlib.asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            if own_engine and not await engine.cache.is_ready():
                raise RuntimeError("Cache backend readiness check failed")
            yield
        finally:
            if own_engine:
                await engine.aclose()

    try:
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
    except BaseException:
        if own_engine:
            _run_rollback(engine.aclose)
        raise
    _LOGGER.info("Server %r ready.", _NAME)
    return server
