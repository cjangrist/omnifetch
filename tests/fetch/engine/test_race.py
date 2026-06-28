"""Tests for fetch race execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from omnifetch.fetch.engine.race import (
    AlternativeFetchResult,
    FetchRaceResult,
    run_fetch_race,
)
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError


@dataclass(frozen=True, slots=True)
class _ProviderBehavior:
    """Fake provider behavior for race tests."""

    result: FetchResult | None = None
    error: Exception | None = None
    delay_s: float = 0.0


class _FakeDispatcher:
    """In-memory fetch dispatcher for race tests."""

    def __init__(
        self,
        behaviors: dict[str, _ProviderBehavior],
        active_names: list[str] | None = None,
    ) -> None:
        self._behaviors = behaviors
        self._active_names = (
            list(behaviors) if active_names is None else active_names
        )
        self.calls: list[str] = []
        self.cancelled: list[str] = []

    @property
    def active_names(self) -> list[str]:
        return self._active_names

    async def fetch_url(
        self,
        url: str,
        provider: str | None = None,
    ) -> FetchResult:
        if provider is None:
            raise ProviderError(
                ErrorType.INVALID_INPUT,
                "provider is required",
                "fetch",
            )
        self.calls.append(provider)
        behavior = self._behaviors[provider]
        try:
            if behavior.delay_s:
                await asyncio.sleep(behavior.delay_s)
        except asyncio.CancelledError:
            self.cancelled.append(provider)
            raise
        if behavior.error is not None:
            raise behavior.error
        if behavior.result is None:
            raise AssertionError(f"missing result for {provider}")
        return behavior.result


def _result(
    provider: str,
    content: str | None = None,
    url: str = "https://example.test/page",
) -> FetchResult:
    return FetchResult(
        url=url,
        title=provider.title(),
        content=content or f"# {provider}\n\n" + ("useful content " * 30),
        source_provider=provider,
    )


def _provider_error(provider: str, error_type: ErrorType) -> ProviderError:
    return ProviderError(error_type, f"{provider} failed", provider)


async def test_explicit_provider_returns_race_result() -> None:
    dispatcher = _FakeDispatcher(
        {"firecrawl": _ProviderBehavior(_result("firecrawl"))}
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        provider="firecrawl",
    )

    assert result == FetchRaceResult(
        requested_url="https://example.test/page",
        total_duration_ms=result.total_duration_ms,
        provider_used="firecrawl",
        providers_attempted=("firecrawl",),
        providers_failed=(),
        result=_result("firecrawl"),
    )
    assert dispatcher.calls == ["firecrawl"]


async def test_explicit_provider_rejects_blocked_content() -> None:
    dispatcher = _FakeDispatcher(
        {"tavily": _ProviderBehavior(_result("tavily", content="short"))}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            provider="tavily",
        )

    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == "Blocked or empty (5 chars)"


async def test_explicit_provider_wraps_unexpected_exception() -> None:
    dispatcher = _FakeDispatcher(
        {"firecrawl": _ProviderBehavior(error=RuntimeError("transport failed"))}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            provider="firecrawl",
        )

    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == "transport failed"
    assert error_info.value.provider == "firecrawl"


async def test_explicit_provider_preserves_provider_error() -> None:
    original_error = ProviderError(
        ErrorType.API_ERROR,
        "upstream rejected the request",
        "firecrawl",
    )
    dispatcher = _FakeDispatcher(
        {"firecrawl": _ProviderBehavior(error=original_error)}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            provider="firecrawl",
        )

    assert error_info.value is original_error


async def test_provider_and_skip_providers_are_mutually_exclusive() -> None:
    dispatcher = _FakeDispatcher(
        {"tavily": _ProviderBehavior(_result("tavily"))}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            provider="tavily",
            skip_providers=("tavily",),
        )

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == (
        "provider and skip_providers are mutually exclusive"
    )


async def test_unknown_explicit_provider_is_rejected() -> None:
    dispatcher = _FakeDispatcher(
        {"tavily": _ProviderBehavior(_result("tavily"))}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            provider="bogus",
        )

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "Unknown explicit provider: bogus"
    assert dispatcher.calls == []


async def test_unknown_skip_provider_is_rejected() -> None:
    dispatcher = _FakeDispatcher(
        {"tavily": _ProviderBehavior(_result("tavily"))}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            skip_providers=("bogus",),
        )

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "Unknown skip_providers: bogus"


@pytest.mark.parametrize(
    ("active_names", "skip_providers", "message"),
    [
        ([], (), "No fetch providers available - no providers configured"),
        (
            ["tavily"],
            ("tavily",),
            "No fetch providers available - all candidates skipped "
            "via skip_providers (tavily)",
        ),
    ],
)
async def test_empty_active_set_is_invalid(
    active_names: list[str],
    skip_providers: tuple[str, ...],
    message: str,
) -> None:
    dispatcher = _FakeDispatcher(
        {"tavily": _ProviderBehavior(_result("tavily"))},
        active_names=active_names,
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            skip_providers=skip_providers,
        )

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == message


async def test_waterfall_falls_through_failed_solo_provider() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily", content="short")),
            "firecrawl": _ProviderBehavior(_result("firecrawl")),
        }
    )

    result = await run_fetch_race(dispatcher, "https://example.test/page")

    assert result.provider_used == "firecrawl"
    assert result.providers_attempted == ("tavily", "firecrawl")
    assert [failure.provider for failure in result.providers_failed] == [
        "tavily"
    ]
    assert dispatcher.calls == ["tavily", "firecrawl"]


async def test_inactive_waterfall_providers_are_skipped() -> None:
    dispatcher = _FakeDispatcher(
        {"firecrawl": _ProviderBehavior(_result("firecrawl"))}
    )

    result = await run_fetch_race(dispatcher, "https://example.test/page")

    assert result.provider_used == "firecrawl"
    assert result.providers_attempted == ("firecrawl",)
    assert dispatcher.calls == ["firecrawl"]


async def test_matching_breaker_runs_before_waterfall() -> None:
    dispatcher = _FakeDispatcher(
        {
            "github": _ProviderBehavior(
                _result("github", content="tiny gist", url="https://github.com")
            ),
            "tavily": _ProviderBehavior(_result("tavily")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://github.com/cjangrist/omnifetch",
    )

    assert result.provider_used == "github"
    assert result.providers_attempted == ("github",)
    assert dispatcher.calls == ["github"]


async def test_failed_breaker_falls_through_to_next_matching_breaker() -> None:
    dispatcher = _FakeDispatcher(
        {
            "supadata": _ProviderBehavior(
                error=_provider_error("supadata", ErrorType.API_ERROR)
            ),
            "sociavault": _ProviderBehavior(_result("sociavault")),
            "tavily": _ProviderBehavior(_result("tavily")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://www.youtube.com/watch?v=abc",
    )

    assert result.provider_used == "sociavault"
    assert result.providers_attempted == ("supadata", "sociavault")
    assert [failure.provider for failure in result.providers_failed] == [
        "supadata"
    ]


async def test_later_breaker_not_found_preserves_prior_success() -> None:
    dispatcher = _FakeDispatcher(
        {
            "supadata": _ProviderBehavior(_result("supadata")),
            "sociavault": _ProviderBehavior(
                error=_provider_error("sociavault", ErrorType.NOT_FOUND)
            ),
            "tavily": _ProviderBehavior(_result("tavily")),
            "firecrawl": _ProviderBehavior(_result("firecrawl")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://www.youtube.com/watch?v=abc",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "supadata"
    assert result.alternative_results == ()
    assert result.providers_attempted == ("supadata", "sociavault")
    assert [failure.provider for failure in result.providers_failed] == [
        "sociavault"
    ]
    assert dispatcher.calls == ["supadata", "sociavault"]


async def test_not_found_fast_fails_waterfall() -> None:
    dispatcher = _FakeDispatcher(
        {
            "github": _ProviderBehavior(
                error=_provider_error("github", ErrorType.NOT_FOUND)
            ),
            "tavily": _ProviderBehavior(_result("tavily")),
        }
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(dispatcher, "https://github.com/missing/repo")

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert dispatcher.calls == ["github"]


async def test_skip_provider_uses_next_active_candidate() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "firecrawl": _ProviderBehavior(_result("firecrawl")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "firecrawl"
    assert result.providers_attempted == ("firecrawl",)
    assert dispatcher.calls == ["firecrawl"]


async def test_parallel_step_collects_two_winners_for_skip_provider() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "linkup": _ProviderBehavior(_result("linkup"), delay_s=0.01),
            "cloudflare_browser": _ProviderBehavior(
                _result("cloudflare_browser"),
                delay_s=0.02,
            ),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "linkup"
    assert result.alternative_results == (
        AlternativeFetchResult(
            "cloudflare_browser",
            _result("cloudflare_browser"),
        ),
    )
    assert result.providers_attempted == ("linkup", "cloudflare_browser")


async def test_parallel_same_tick_successes_use_configured_order() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "scrapfly": _ProviderBehavior(_result("scrapfly")),
            "scrapedo": _ProviderBehavior(_result("scrapedo")),
            "decodo": _ProviderBehavior(_result("decodo")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "scrapfly"
    assert result.alternative_results == (
        AlternativeFetchResult("scrapedo", _result("scrapedo")),
    )
    assert result.providers_attempted == ("scrapfly", "scrapedo", "decodo")
    assert dispatcher.cancelled == []


async def test_parallel_step_records_failures_before_success() -> None:
    dispatcher = _FakeDispatcher(
        {
            "linkup": _ProviderBehavior(
                error=_provider_error("linkup", ErrorType.API_ERROR)
            ),
            "cloudflare_browser": _ProviderBehavior(
                _result("cloudflare_browser"),
                delay_s=0.01,
            ),
        }
    )

    result = await run_fetch_race(dispatcher, "https://example.test/page")

    assert result.provider_used == "cloudflare_browser"
    assert result.providers_attempted == ("linkup", "cloudflare_browser")
    assert [failure.provider for failure in result.providers_failed] == [
        "linkup"
    ]


async def test_parallel_success_wins_over_same_tick_not_found() -> None:
    dispatcher = _FakeDispatcher(
        {
            "linkup": _ProviderBehavior(_result("linkup")),
            "cloudflare_browser": _ProviderBehavior(
                error=_provider_error(
                    "cloudflare_browser",
                    ErrorType.NOT_FOUND,
                )
            ),
        }
    )

    result = await run_fetch_race(dispatcher, "https://example.test/page")

    assert result.provider_used == "linkup"
    assert result.providers_attempted == ("linkup", "cloudflare_browser")
    assert [failure.provider for failure in result.providers_failed] == [
        "cloudflare_browser"
    ]


async def test_parallel_not_found_after_success_returns_primary() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "linkup": _ProviderBehavior(_result("linkup")),
            "cloudflare_browser": _ProviderBehavior(
                error=_provider_error(
                    "cloudflare_browser",
                    ErrorType.NOT_FOUND,
                ),
                delay_s=0.01,
            ),
            "scrapfly": _ProviderBehavior(_result("scrapfly")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "linkup"
    assert result.alternative_results == ()
    assert result.providers_attempted == ("linkup", "cloudflare_browser")
    assert [failure.provider for failure in result.providers_failed] == [
        "cloudflare_browser"
    ]
    assert dispatcher.calls == ["linkup", "cloudflare_browser"]


async def test_later_waterfall_not_found_preserves_prior_success() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "firecrawl": _ProviderBehavior(
                error=_provider_error("firecrawl", ErrorType.NOT_FOUND)
            ),
            "kimi": _ProviderBehavior(_result("kimi")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("kimi",),
    )

    assert result.provider_used == "tavily"
    assert result.alternative_results == ()
    assert result.providers_attempted == ("tavily", "firecrawl")
    assert [failure.provider for failure in result.providers_failed] == [
        "firecrawl"
    ]


async def test_parallel_not_found_cancels_pending_and_fast_fails() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "linkup": _ProviderBehavior(
                error=_provider_error("linkup", ErrorType.NOT_FOUND)
            ),
            "cloudflare_browser": _ProviderBehavior(
                _result("cloudflare_browser"),
                delay_s=1.0,
            ),
        }
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            skip_providers=("tavily",),
        )

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert dispatcher.calls == ["linkup", "cloudflare_browser"]
    assert dispatcher.cancelled == ["cloudflare_browser"]


async def test_parallel_success_cancels_excess_healthy_providers() -> None:
    dispatcher = _FakeDispatcher(
        {
            "scrapfly": _ProviderBehavior(_result("scrapfly"), delay_s=0.01),
            "scrapedo": _ProviderBehavior(_result("scrapedo"), delay_s=1.0),
            "decodo": _ProviderBehavior(_result("decodo"), delay_s=1.0),
        }
    )

    result = await run_fetch_race(dispatcher, "https://example.test/page")

    assert result.provider_used == "scrapfly"
    assert result.alternative_results == ()
    assert result.providers_attempted == ("scrapfly", "scrapedo", "decodo")
    assert result.providers_failed == ()
    assert sorted(dispatcher.cancelled) == ["decodo", "scrapedo"]


async def test_outer_cancellation_cleans_up_parallel_tasks() -> None:
    dispatcher = _FakeDispatcher(
        {
            "linkup": _ProviderBehavior(_result("linkup"), delay_s=1.0),
            "cloudflare_browser": _ProviderBehavior(
                _result("cloudflare_browser"),
                delay_s=1.0,
            ),
        }
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            run_fetch_race(dispatcher, "https://example.test/page"),
            timeout=0.01,
        )

    assert sorted(dispatcher.cancelled) == [
        "cloudflare_browser",
        "linkup",
    ]


async def test_sequential_step_collects_two_winners_for_skip_provider() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "jina": _ProviderBehavior(_result("jina")),
            "spider": _ProviderBehavior(_result("spider")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "jina"
    assert result.alternative_results == (
        AlternativeFetchResult("spider", _result("spider")),
    )
    assert result.providers_attempted == ("jina", "spider")


async def test_sequential_not_found_without_success_fast_fails() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "jina": _ProviderBehavior(
                error=_provider_error("jina", ErrorType.NOT_FOUND)
            ),
            "spider": _ProviderBehavior(_result("spider")),
        }
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(
            dispatcher,
            "https://example.test/page",
            skip_providers=("tavily",),
        )

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert dispatcher.calls == ["jina"]


async def test_sequential_not_found_after_success_returns_primary() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "jina": _ProviderBehavior(_result("jina")),
            "spider": _ProviderBehavior(
                error=_provider_error("spider", ErrorType.NOT_FOUND)
            ),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "jina"
    assert result.alternative_results == ()
    assert result.providers_attempted == ("jina", "spider")
    assert [failure.provider for failure in result.providers_failed] == [
        "spider"
    ]


async def test_skip_provider_collects_second_solo_winner() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(_result("tavily")),
            "firecrawl": _ProviderBehavior(_result("firecrawl")),
            "kimi": _ProviderBehavior(_result("kimi")),
        }
    )

    result = await run_fetch_race(
        dispatcher,
        "https://example.test/page",
        skip_providers=("tavily",),
    )

    assert result.provider_used == "firecrawl"
    assert result.alternative_results == (
        AlternativeFetchResult("kimi", _result("kimi")),
    )
    assert result.providers_attempted == ("firecrawl", "kimi")


async def test_waterfall_exhaustion_reports_attempts() -> None:
    dispatcher = _FakeDispatcher(
        {
            "tavily": _ProviderBehavior(
                error=_provider_error("tavily", ErrorType.API_ERROR)
            ),
            "firecrawl": _ProviderBehavior(
                error=RuntimeError("transport failed")
            ),
        }
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(dispatcher, "https://example.test/page")

    assert error_info.value.error_type is ErrorType.PROVIDER_ERROR
    assert str(error_info.value) == (
        "All providers failed for https://example.test/page. "
        "Tried: tavily, firecrawl"
    )
    details = error_info.value.details
    assert isinstance(details, tuple)
    assert len(details) == 2
    assert [failure.provider for failure in details] == [
        "tavily",
        "firecrawl",
    ]
    assert [failure.error for failure in details] == [
        "tavily failed",
        "transport failed",
    ]


async def test_no_eligible_topology_provider_is_invalid() -> None:
    dispatcher = _FakeDispatcher(
        {"github": _ProviderBehavior(_result("github"))}
    )

    with pytest.raises(ProviderError) as error_info:
        await run_fetch_race(dispatcher, "https://example.test/page")

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == (
        "No active fetch provider is eligible for https://example.test/page"
    )
    assert dispatcher.calls == []
