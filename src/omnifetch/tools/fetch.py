"""The ``fetch`` tool: multi-provider URL to markdown waterfall."""

from __future__ import annotations

import logging

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from logdecorator.asyncio import async_log_on_end, async_log_on_start
from mcp.types import ToolAnnotations

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

_TOOL_NAME = "fetch"
_TOOL_TITLE = "URL Fetch (multi-provider waterfall)"
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
            "fetch",
        )
    return valid


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


async def execute_fetch(
    engine: Engine,
    url: str,
    *,
    provider: str | None = None,
    skip_providers: str | list[str] | None = None,
) -> FetchResponse:
    """Fetch a URL through the shared engine and return a flat response."""
    skip = _parse_valid_skip_providers(
        skip_providers,
        engine.unified.active_names,
    )
    race = await run_fetch_race(
        engine.unified,
        url,
        provider=provider,
        skip_providers=skip,
    )
    return _to_response(race)


def register_fetch_tool(server: FastMCP, engine: Engine) -> None:
    """Register the ``fetch`` tool on the given FastMCP server."""

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
    async def fetch(
        url: FetchUrl,
        skip_providers: SkipProviders = None,
        ctx: Context | None = None,
    ) -> FetchResponse:
        try:
            return await execute_fetch(
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
    )(fetch)
