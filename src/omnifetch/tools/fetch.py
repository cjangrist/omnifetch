"""The ``web_fetch`` tool: multi-provider URL to markdown waterfall."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from urllib.parse import urlsplit

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from logdecorator.asyncio import async_log_on_end, async_log_on_start
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from omnifetch.fetch.engine.race import (
    AlternativeFetchResult,
    FetchRaceResult,
    ProviderAttemptFailure,
    run_fetch_race,
)
from omnifetch.fetch.engine.runtime import Engine
from omnifetch.fetch.engine.skip import (
    parse_skip_providers,
    validate_skip_providers,
)
from omnifetch.fetch.shared.types import ErrorType, ProviderError
from omnifetch.logging import get_logger
from omnifetch.schemas import (
    FetchAlternative,
    FetchProviderFailure,
    FetchResponse,
    FetchUrl,
    SkipProviders,
)

_LOGGER = get_logger("tools.fetch")

_TOOL_NAME = "web_fetch"
_FETCH_CACHE_NAMESPACE = "omnifetch:fetch:v1"
_VOLATILE_URL_PATHS = frozenset({"", "/"})
_TOOL_TITLE = "Web Fetch (multi-provider waterfall)"
_TOOL_DESCRIPTION = (
    "Fetch clean markdown from a public URL through the multi-provider "
    "waterfall. If returned content is missing, incomplete, or wrong for the "
    "page, retry the same URL with skip_providers set to the prior "
    "source_provider. skip_providers accepts a comma-separated string, "
    "JSON-encoded array string, or native array, and can return an "
    "alternative result for comparison when enough providers are available."
)
_TOOL_ANNOTATIONS = ToolAnnotations(
    title=_TOOL_TITLE,
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _parse_valid_skip_providers(
    raw_skip_providers: str | list[str] | None,
    active_names: list[str],
) -> list[str]:
    """Parse and validate skip-provider input against active providers."""
    parsed = parse_skip_providers(raw_skip_providers)
    if not parsed:
        return []

    valid, unknown = validate_skip_providers(parsed, active_names)
    if unknown:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"Unknown skip_providers names: {', '.join(unknown)}. "
            f"Valid: {', '.join(active_names)}",
            _TOOL_NAME,
        )
    valid_names = set(valid)
    return [name for name in active_names if name in valid_names]


def _validate_provider_controls(
    provider: str | None,
    skip_providers: list[str],
    active_names: list[str],
) -> None:
    """Reject invalid provider controls before consulting the cache."""
    if provider is not None and skip_providers:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            "provider and skip_providers are mutually exclusive",
            "waterfall",
        )
    if provider is not None and provider not in active_names:
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"Unknown explicit provider: {provider}",
            "waterfall",
        )
    skip_names = set(skip_providers)
    eligible_names = [name for name in active_names if name not in skip_names]
    if provider is None and not eligible_names:
        skipped = ", ".join(skip_providers)
        reason = (
            f"all candidates skipped via skip_providers ({skipped})"
            if skip_providers
            else "no providers configured"
        )
        raise ProviderError(
            ErrorType.INVALID_INPUT,
            f"No fetch providers available - {reason}",
            "waterfall",
        )


def _fetch_cache_key(
    url: str,
    provider: str | None,
    skip_providers: list[str],
) -> str:
    """Return a versioned digest of the exact effective fetch request."""
    identity = {
        "provider": provider,
        "skip_providers": skip_providers,
        "url": url,
    }
    canonical = json.dumps(
        identity,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return f"{_FETCH_CACHE_NAMESPACE}:{digest}"


def _cache_key_reference(key: str) -> str:
    """Return the safe digest prefix from a versioned fetch key."""
    return key.rsplit(":", maxsplit=1)[-1][:12]


def _cache_identity_url(engine: Engine, url: str) -> str:
    """Return the URL spelling two requests must share to be one entry.

    The canonicalizer is supplied by whoever built the engine, so it is
    treated as foreign code on a paying path. Anything it raises falls back to
    the URL as given rather than turning a fetch into an error; an empty result
    is refused because it would collapse every distinct URL onto one key; and a
    non-string is refused because the annotation is only a promise -- a real
    canonicalizer often works in ``httpx.URL`` or ``yarl.URL``, and returning
    one would reach ``json.dumps`` and raise on the very path this function
    exists to keep safe.

    Every rejection is logged. Falling back silently would leave an operator
    paying for duplicate fetches with nothing to explain why the canonicalizer
    they configured appears to do nothing. The reason and the result's type are
    bounded values; the result itself is never logged.
    """
    try:
        canonical: object = engine.canonicalize_cache_url(url)
    except Exception as error:
        _LOGGER.warning(
            "Fetch cache URL canonicalization failed (%s)",
            type(error).__name__,
        )
        return url
    if isinstance(canonical, str) and canonical:
        return canonical
    reason = (
        "empty result"
        if isinstance(canonical, str)
        else f"result of type {type(canonical).__name__}"
    )
    _LOGGER.warning("Fetch cache URL canonicalization rejected (%s)", reason)
    return url


def is_volatile_fetch_url(url: str) -> bool:
    """Report whether a URL is a site homepage rather than a stable page.

    A homepage is a rolling index. The masthead of a news site is rewritten
    many times an hour while an article underneath it never changes again, so
    one lifetime cannot serve both: it either hands back a stale front page or
    throws away an article that was still perfectly reusable.

    The test is structural -- an empty or root path -- rather than a list of
    known news domains. A list needs maintaining, silently misses every site
    absent from it, and still cannot tell a homepage from an article on a
    domain it does recognise. An empty path is a homepage everywhere.

    The classification is total. ``urlsplit`` raises on a malformed authority
    such as ``http://[::1``, and this runs after a provider has already been
    paid, so an unparseable URL must not turn a fetch that succeeded into an
    exception. It is reported as non-volatile: this predicate claims a
    homepage only when it can prove one, and the ordinary lifetime is the
    default everything else already gets.
    """
    try:
        path = urlsplit(url).path
    except ValueError:
        return False
    return path in _VOLATILE_URL_PATHS


def _fetch_cache_ttl_seconds(engine: Engine, url: str) -> int:
    """Return how long this URL's fetched content may be reused.

    A volatile URL is never allowed to outlive an ordinary one: an operator
    who shortens the main TTL below the volatile TTL means everything to be
    fresher, not homepages to become the stalest entries in the cache.
    """
    if not is_volatile_fetch_url(url):
        return engine.fetch_cache_ttl_seconds
    return min(
        engine.volatile_fetch_cache_ttl_seconds,
        engine.fetch_cache_ttl_seconds,
    )


def _cache_hit_duration_ms(start_time: float) -> int:
    """Return current-request elapsed milliseconds for one cache hit."""
    return round((time.monotonic() - start_time) * 1000)


def _response_for_request(
    response: FetchResponse,
    start_time: float,
) -> FetchResponse:
    """Return a defensive response copy with caller-specific timing."""
    return response.model_copy(
        deep=True,
        update={"total_duration_ms": _cache_hit_duration_ms(start_time)},
    )


async def _discard_invalid_cache_entry(engine: Engine, key: str) -> None:
    """Best-effort delete one corrupt or incompatible fetch entry."""
    try:
        await engine.cache.delete(key)
    except Exception as error:
        _LOGGER.warning(
            "Fetch cache cleanup failed for key %s (%s)",
            _cache_key_reference(key),
            type(error).__name__,
        )


async def _read_fetch_cache(
    engine: Engine,
    key: str,
) -> FetchResponse | None:
    """Return one strictly validated fetch response or a cache miss."""
    key_reference = _cache_key_reference(key)
    try:
        cached = await engine.cache.get(key)
    except Exception as error:
        _LOGGER.warning(
            "Fetch cache read failed for key %s (%s)",
            key_reference,
            type(error).__name__,
        )
        return None
    if cached is None:
        _LOGGER.debug("Fetch cache miss for key %s", key_reference)
        return None
    try:
        response = FetchResponse.model_validate(cached)
    except (TypeError, ValidationError) as error:
        _LOGGER.warning(
            "Fetch cache entry invalid for key %s (%s)",
            key_reference,
            type(error).__name__,
        )
        await _discard_invalid_cache_entry(engine, key)
        return None
    _LOGGER.debug("Fetch cache hit for key %s", key_reference)
    return response


async def _write_fetch_cache(
    engine: Engine,
    key: str,
    response: FetchResponse,
    ttl_seconds: int,
) -> None:
    """Best-effort store one successful, validated fetch response."""
    key_reference = _cache_key_reference(key)
    try:
        stored = await engine.cache.set(
            key,
            response.model_dump(mode="json"),
            ttl_seconds,
        )
    except Exception as error:
        _LOGGER.warning(
            "Fetch cache write failed for key %s (%s)",
            key_reference,
            type(error).__name__,
        )
        return
    _LOGGER.debug(
        "Fetch cache write %s for key %s",
        "stored" if stored else "skipped",
        key_reference,
    )


def _claim_fetch_flight(
    engine: Engine,
    key: str,
) -> tuple[bool, asyncio.Future[FetchResponse | None]]:
    """Return whether this caller leads the in-process fetch flight."""
    existing = engine.fetch_flights.get(key)
    if existing is not None:
        return False, existing
    completion = asyncio.get_running_loop().create_future()
    engine.fetch_flights[key] = completion
    return True, completion


def _release_fetch_flight(
    engine: Engine,
    key: str,
    completion: asyncio.Future[FetchResponse | None],
    response: FetchResponse | None,
) -> None:
    """Release one flight and publish its validated response to waiters."""
    if engine.fetch_flights.get(key) is completion:
        del engine.fetch_flights[key]
    if not completion.done():
        completion.set_result(response)


async def _fetch_and_store(
    engine: Engine,
    url: str,
    provider: str | None,
    skip_providers: list[str],
    cache_key: str,
) -> FetchResponse:
    """Run one provider race and cache only its validated success response."""
    race = await run_fetch_race(
        engine.unified,
        url,
        provider=provider,
        skip_providers=skip_providers,
    )
    response = _to_response(race)
    await _write_fetch_cache(
        engine,
        cache_key,
        response,
        _fetch_cache_ttl_seconds(engine, url),
    )
    return response


def _failure_to_response(
    failure: ProviderAttemptFailure,
) -> FetchProviderFailure:
    """Convert one race failure into the public response schema."""
    return FetchProviderFailure(
        provider=failure.provider,
        error=failure.error,
        duration_ms=failure.duration_ms,
    )


def _alternative_to_response(
    alternative: AlternativeFetchResult,
) -> FetchAlternative:
    """Convert one race alternative into the public response schema."""
    return FetchAlternative(
        url=alternative.result.url,
        title=alternative.result.title,
        content=alternative.result.content,
        source_provider=alternative.provider,
        metadata=alternative.result.metadata,
    )


def _to_response(race: FetchRaceResult) -> FetchResponse:
    """Flatten a race result into the public fetch response schema."""
    alternatives = [
        _alternative_to_response(alternative)
        for alternative in race.alternative_results
    ]
    return FetchResponse(
        url=race.result.url,
        title=race.result.title,
        content=race.result.content,
        source_provider=race.provider_used,
        total_duration_ms=race.total_duration_ms,
        metadata=race.result.metadata,
        providers_attempted=list(race.providers_attempted),
        providers_failed=[
            _failure_to_response(failure) for failure in race.providers_failed
        ],
        alternative_results=alternatives or None,
    )


async def execute_web_fetch(
    engine: Engine,
    url: str,
    *,
    provider: str | None = None,
    skip_providers: str | list[str] | None = None,
) -> FetchResponse:
    """Return a cached success or fetch through the shared provider engine.

    Leadership is confirmed with a second cache read before any provider is
    paid. The first read awaits the backend, and a leader that finishes during
    that await leaves this caller holding a miss that is already stale: it
    would claim the now-free flight and buy a page the cache is holding. The
    re-read closes that window, so a duplicate fetch needs the entry to be
    genuinely absent at the moment leadership is taken.
    """
    request_start_time = time.monotonic()
    normalized_url = url.strip()
    active_names = engine.unified.active_names
    skip = _parse_valid_skip_providers(
        skip_providers,
        active_names,
    )
    _validate_provider_controls(provider, skip, active_names)
    cache_key = _fetch_cache_key(
        _cache_identity_url(engine, normalized_url), provider, skip
    )

    while True:
        cached = await _read_fetch_cache(engine, cache_key)
        if cached is not None:
            return _response_for_request(cached, request_start_time)
        is_leader, completion = _claim_fetch_flight(engine, cache_key)
        if is_leader:
            break
        _LOGGER.debug(
            "Fetch cache miss coalesced for key %s",
            _cache_key_reference(cache_key),
        )
        shared_response = await asyncio.shield(completion)
        if shared_response is not None:
            return _response_for_request(shared_response, request_start_time)

    response: FetchResponse | None = None
    try:
        settled = await _read_fetch_cache(engine, cache_key)
        if settled is not None:
            return _response_for_request(settled, request_start_time)
        response = await _fetch_and_store(
            engine,
            normalized_url,
            provider,
            skip,
            cache_key,
        )
        return response
    finally:
        _release_fetch_flight(engine, cache_key, completion, response)


def register_web_fetch_tool(server: FastMCP, engine: Engine) -> None:
    """Register the ``web_fetch`` tool on the given FastMCP server."""

    @async_log_on_start(
        logging.INFO,
        "Tool call: {callable.__name__}(url={url!r})",
        logger=_LOGGER,
    )
    @async_log_on_end(
        logging.INFO,
        "Tool exit: {callable.__name__}",
        logger=_LOGGER,
    )
    async def web_fetch(
        url: FetchUrl,
        skip_providers: SkipProviders = None,
        ctx: Context | None = None,
    ) -> FetchResponse:
        try:
            return await execute_web_fetch(
                engine,
                url,
                skip_providers=skip_providers,
            )
        except ProviderError as error:
            raise ToolError(str(error)) from error

    server.tool(
        name=_TOOL_NAME,
        title=_TOOL_TITLE,
        description=_TOOL_DESCRIPTION,
        annotations=_TOOL_ANNOTATIONS,
    )(web_fetch)
