"""Fetch race execution over active provider dispatchers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from omnifetch.fetch.engine.failure import is_fetch_failure
from omnifetch.fetch.engine.skip import validate_skip_providers
from omnifetch.fetch.engine.waterfall import (
    BREAKERS,
    matches_breaker,
    Step,
    WATERFALL_STEPS,
)
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError


class FetchDispatcher(Protocol):
    """Provider dispatcher protocol used by the race executor."""

    @property
    def active_names(self) -> list[str]:
        """Return active provider names."""

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        """Fetch ``url`` with an explicit provider."""


@dataclass(frozen=True, slots=True)
class ProviderAttemptFailure:
    """One failed provider attempt in a fetch race."""

    provider: str
    error: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class AlternativeFetchResult:
    """One non-primary successful provider result."""

    provider: str
    result: FetchResult


@dataclass(frozen=True, slots=True)
class FetchRaceResult:
    """Final fetch race result with provider attempt metadata."""

    requested_url: str
    total_duration_ms: int
    provider_used: str
    providers_attempted: tuple[str, ...]
    providers_failed: tuple[ProviderAttemptFailure, ...]
    result: FetchResult
    alternative_results: tuple[AlternativeFetchResult, ...] = ()


@dataclass(slots=True)
class _RaceContext:
    """Mutable state for one fetch race."""

    dispatcher: FetchDispatcher
    url: str
    active: set[str]
    winners: list[_ProviderWinner]
    attempted: list[str]
    failed: list[ProviderAttemptFailure]


@dataclass(frozen=True, slots=True)
class _ProviderWinner:
    """Successful provider and its normalized fetch result."""

    provider: str
    result: FetchResult


def _duration_ms(start_time: float) -> int:
    """Return elapsed monotonic time in milliseconds."""
    return round((time.monotonic() - start_time) * 1000)


def _provider_error(message: str, error_type: ErrorType) -> ProviderError:
    """Return a fetch-waterfall provider error."""
    return ProviderError(error_type, message, "waterfall")


def _all_providers_failed_error(
    url: str,
    attempted: list[str],
    failed: list[ProviderAttemptFailure],
) -> ProviderError:
    """Return the final provider-exhaustion error."""
    if not attempted:
        return _provider_error(
            f"No active fetch provider is eligible for {url[:200]}",
            ErrorType.INVALID_INPUT,
        )
    return ProviderError(
        ErrorType.PROVIDER_ERROR,
        f"All providers failed for {url[:200]}. Tried: {', '.join(attempted)}",
        "waterfall",
        details=tuple(failed),
    )


def _blocked_result_error(provider: str, result: FetchResult) -> ProviderError:
    """Return an error for unusable provider content."""
    return ProviderError(
        ErrorType.PROVIDER_ERROR,
        f"Blocked or empty ({len(result.content)} chars)",
        provider,
    )


def _build_result(
    start_time: float,
    requested_url: str,
    winners: list[_ProviderWinner],
    attempted: list[str],
    failed: list[ProviderAttemptFailure],
) -> FetchRaceResult:
    """Build the public fetch race result from collected winners."""
    primary = winners[0]
    alternatives = tuple(
        AlternativeFetchResult(winner.provider, winner.result)
        for winner in winners[1:]
    )
    return FetchRaceResult(
        requested_url=requested_url,
        total_duration_ms=_duration_ms(start_time),
        provider_used=primary.provider,
        providers_attempted=tuple(attempted),
        providers_failed=tuple(failed),
        result=primary.result,
        alternative_results=alternatives,
    )


def _record_failure(
    ctx: _RaceContext,
    provider: str,
    error: Exception,
    start_time: float,
) -> None:
    """Record a provider failure in race metadata."""
    ctx.failed.append(
        ProviderAttemptFailure(provider, str(error), _duration_ms(start_time))
    )


def _record_winner(
    ctx: _RaceContext,
    provider: str,
    result: FetchResult,
    target_count: int,
) -> None:
    """Record one winner if the race still needs more results."""
    if len(ctx.winners) < target_count:
        ctx.winners.append(_ProviderWinner(provider, result))


async def _fetch_provider(
    ctx: _RaceContext,
    provider: str,
    *,
    record_attempt: bool = True,
) -> FetchResult | None:
    """Attempt one active provider and record non-definitive failures."""
    if provider not in ctx.active:
        return None
    if record_attempt:
        ctx.attempted.append(provider)

    start_time = time.monotonic()
    try:
        result = await ctx.dispatcher.fetch_url(ctx.url, provider=provider)
        if is_fetch_failure(result, provider):
            raise _blocked_result_error(provider, result)
        return result
    except ProviderError as error:
        _record_failure(ctx, provider, error, start_time)
        if error.error_type is ErrorType.NOT_FOUND:
            raise
        return None
    except Exception as error:
        _record_failure(ctx, provider, error, start_time)
        return None


async def _run_solo(
    ctx: _RaceContext,
    provider: str,
    target_count: int,
) -> None:
    """Run one solo provider step."""
    result = await _fetch_provider(ctx, provider)
    if result is not None:
        _record_winner(ctx, provider, result, target_count)


async def _await_parallel_winners(
    ctx: _RaceContext,
    tasks: dict[asyncio.Task[FetchResult | None], str],
    target_count: int,
) -> None:
    """Collect parallel winners until enough succeed or all attempts finish."""
    pending = set(tasks)
    provider_order = {
        provider: index for index, provider in enumerate(tasks.values())
    }
    not_found_error: ProviderError | None = None
    while pending and len(ctx.winners) < target_count:
        done, pending = await asyncio.wait(
            pending,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in sorted(
            done,
            key=lambda completed: provider_order[tasks[completed]],
        ):
            provider = tasks[task]
            try:
                result = task.result()
            except ProviderError as error:
                not_found_error = error
                continue
            if result is not None:
                _record_winner(ctx, provider, result, target_count)
        if not_found_error is not None:
            raise not_found_error


async def _run_parallel(
    ctx: _RaceContext,
    providers: tuple[str, ...],
    target_count: int,
) -> None:
    """Run a parallel provider step and keep up to ``target_count`` winners."""
    available = tuple(
        provider for provider in providers if provider in ctx.active
    )
    if not available:
        return

    ctx.attempted.extend(available)
    tasks = {
        asyncio.create_task(
            _fetch_provider(ctx, provider, record_attempt=False)
        ): provider
        for provider in available
    }
    try:
        await _await_parallel_winners(ctx, tasks, target_count)
    finally:
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def _run_sequential(
    ctx: _RaceContext,
    providers: tuple[str, ...],
    target_count: int,
) -> None:
    """Run sequential providers until enough winners are collected."""
    for provider in providers:
        if len(ctx.winners) >= target_count:
            break
        result = await _fetch_provider(ctx, provider)
        if result is not None:
            _record_winner(ctx, provider, result, target_count)


async def _execute_step(
    ctx: _RaceContext,
    step: Step,
    target_count: int,
) -> None:
    """Execute one configured waterfall step."""
    if step.kind == "solo":
        await _run_solo(ctx, step.providers[0], target_count)
        return
    if step.kind == "parallel":
        await _run_parallel(ctx, step.providers, target_count)
        return
    await _run_sequential(ctx, step.providers, target_count)


def _validate_inputs(
    provider: str | None,
    skip_providers: Iterable[str],
    active_names: tuple[str, ...],
) -> tuple[str, ...]:
    """Validate requested provider controls and return effective skips."""
    parsed_skip = tuple(skip_providers)
    if provider is not None and parsed_skip:
        raise _provider_error(
            "provider and skip_providers are mutually exclusive",
            ErrorType.INVALID_INPUT,
        )
    if provider is not None and provider not in active_names:
        raise _provider_error(
            f"Unknown explicit provider: {provider}",
            ErrorType.INVALID_INPUT,
        )

    valid_skip, unknown_skip = validate_skip_providers(
        list(parsed_skip),
        active_names,
    )
    if unknown_skip:
        raise _provider_error(
            f"Unknown skip_providers: {', '.join(unknown_skip)}",
            ErrorType.INVALID_INPUT,
        )
    return tuple(valid_skip)


def _active_after_skip(
    active_names: tuple[str, ...],
    skip_providers: tuple[str, ...],
) -> set[str]:
    """Return active provider names after skip filtering."""
    skip_set = set(skip_providers)
    active = {name for name in active_names if name not in skip_set}
    if active:
        return active

    skipped = ", ".join(skip_providers)
    reason = (
        f"all candidates skipped via skip_providers ({skipped})"
        if skip_providers
        else "no providers configured"
    )
    raise _provider_error(
        f"No fetch providers available - {reason}",
        ErrorType.INVALID_INPUT,
    )


async def _run_explicit_provider(
    dispatcher: FetchDispatcher,
    url: str,
    provider: str,
    start_time: float,
) -> FetchRaceResult:
    """Run one explicitly selected provider."""
    try:
        result = await dispatcher.fetch_url(url, provider=provider)
    except ProviderError:
        raise
    except Exception as error:
        raise ProviderError(
            ErrorType.PROVIDER_ERROR,
            str(error),
            provider,
        ) from error
    if is_fetch_failure(result, provider):
        raise _blocked_result_error(provider, result)
    return FetchRaceResult(
        requested_url=url,
        total_duration_ms=_duration_ms(start_time),
        provider_used=provider,
        providers_attempted=(provider,),
        providers_failed=(),
        result=result,
    )


async def run_fetch_race(
    dispatcher: FetchDispatcher,
    url: str,
    *,
    provider: str | None = None,
    skip_providers: Iterable[str] = (),
) -> FetchRaceResult:
    """Run explicit fetch dispatch or the breaker-first provider waterfall."""
    start_time = time.monotonic()
    active_names = tuple(dispatcher.active_names)
    effective_skip = _validate_inputs(provider, skip_providers, active_names)
    if provider is not None:
        return await _run_explicit_provider(
            dispatcher,
            url,
            provider,
            start_time,
        )

    active = _active_after_skip(active_names, effective_skip)
    target_count = min(2 if effective_skip else 1, len(active))
    ctx = _RaceContext(dispatcher, url, active, [], [], [])
    breaker_not_found_after_success = False

    for breaker in BREAKERS:
        if len(ctx.winners) >= target_count:
            break
        if matches_breaker(url, breaker):
            try:
                await _run_solo(ctx, breaker.provider, target_count)
            except ProviderError as error:
                if error.error_type is ErrorType.NOT_FOUND and ctx.winners:
                    breaker_not_found_after_success = True
                    break
                raise

    if not breaker_not_found_after_success:
        for step in WATERFALL_STEPS:
            if len(ctx.winners) >= target_count:
                break
            try:
                await _execute_step(ctx, step, target_count)
            except ProviderError as error:
                if error.error_type is ErrorType.NOT_FOUND and ctx.winners:
                    break
                raise

    if ctx.winners:
        return _build_result(
            start_time, url, ctx.winners, ctx.attempted, ctx.failed
        )

    raise _all_providers_failed_error(url, ctx.attempted, ctx.failed)
