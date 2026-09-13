"""Tests for the fastCRW fetch provider."""

from __future__ import annotations

import importlib
import json
from typing import cast

import httpx
import pytest
import respx

import omnifetch.fetch.providers.fastcrw as fastcrw_module
import omnifetch.fetch.providers.fastcrw_selfhosted as selfhosted_module
import omnifetch.fetch.providers.firecrawl as firecrawl_module
import omnifetch.fetch.providers.tavily as tavily_module
from omnifetch.fetch.engine.race import run_fetch_race
from omnifetch.fetch.providers import (
    base,
    get_active_fetch_providers,
    UnifiedFetchProvider,
)
from omnifetch.fetch.providers.fastcrw import (
    _DEFINITELY_MISSING_STATUSES,
    FastcrwFetchProvider,
)
from omnifetch.fetch.providers.fastcrw_selfhosted import (
    SelfHostedFastcrwFetchProvider,
)
from omnifetch.fetch.shared.config import ProviderSecrets
from omnifetch.fetch.shared.types import ErrorType, FetchResult, ProviderError

_SCRAPE_URL = "https://api.fastcrw.com/v1/scrape"
_ARTICLE_URL = "https://example.test/article"
_NO_CONTENT_MESSAGE = (
    "Failed to fetch URL content: fastCRW scrape returned no content"
)


def _json_request(request: httpx.Request) -> dict[str, object]:
    """Decode a mocked JSON request body."""
    payload = json.loads(request.content.decode())
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def _provider(client: httpx.AsyncClient) -> FastcrwFetchProvider:
    """Return a configured provider bound to the mocked client."""
    return FastcrwFetchProvider(
        ProviderSecrets({"CRW_API_KEY": "fastcrw-secret"}),
        client,
    )


async def test_fastcrw_fetches_markdown() -> None:
    with respx.mock(assert_all_called=True) as router:

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer fastcrw-secret"
            assert _json_request(request) == {
                "url": _ARTICLE_URL,
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "markdown": "# fastCRW\n\nBody",
                        "renderDecision": {
                            "kind": "autoDefault",
                            "chosen": "http",
                        },
                        "creditCost": 1,
                        "metadata": {
                            "title": "fastCRW",
                            "description": "Extracted page",
                            "sourceURL": "https://canonical.example/article",
                            "statusCode": 200,
                        },
                    },
                },
                request=request,
            )

        router.post(_SCRAPE_URL).mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            result = await _provider(client).fetch_url(_ARTICLE_URL)

    assert result == FetchResult(
        url="https://canonical.example/article",
        title="fastCRW",
        content="# fastCRW\n\nBody",
        source_provider="fastcrw",
        metadata={"description": "Extracted page", "status_code": 200},
    )


async def test_fastcrw_uses_url_fallback_without_metadata() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={"success": True, "data": {"markdown": "# fastCRW\n\nBody"}}
        )
        async with httpx.AsyncClient() as client:
            result = await _provider(client).fetch_url(_ARTICLE_URL)

    assert result == FetchResult(
        url=_ARTICLE_URL,
        title="",
        content="# fastCRW\n\nBody",
        source_provider="fastcrw",
    )


async def test_fastcrw_falls_back_within_partial_metadata() -> None:
    """Metadata that omits the canonical URL and title still yields content.

    The empty strings must come from the request URL and the title default
    rather than from the metadata block, which is present but unhelpful.
    """
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={
                "success": True,
                "data": {
                    "markdown": "# fastCRW\n\nBody",
                    "metadata": {"statusCode": 200},
                },
            }
        )
        async with httpx.AsyncClient() as client:
            result = await _provider(client).fetch_url(_ARTICLE_URL)

    assert result == FetchResult(
        url=_ARTICLE_URL,
        title="",
        content="# fastCRW\n\nBody",
        source_provider="fastcrw",
        metadata={"description": None, "status_code": 200},
    )


async def test_fastcrw_requires_key() -> None:
    async with httpx.AsyncClient() as client:
        provider = FastcrwFetchProvider(ProviderSecrets({}), client)
        with pytest.raises(ProviderError) as error_info:
            await provider.fetch_url(_ARTICLE_URL)

    assert error_info.value.error_type is ErrorType.INVALID_INPUT
    assert str(error_info.value) == "API key not found for fastcrw"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"success": False, "data": {"markdown": "# Nope"}},
            "Failed to fetch URL content: fastCRW scrape failed",
        ),
        (
            {"success": False, "error": "Proxy pool exhausted"},
            "Failed to fetch URL content: fastCRW scrape failed: "
            "Proxy pool exhausted",
        ),
        ({"success": True}, _NO_CONTENT_MESSAGE),
        ({"success": True, "data": {"markdown": ""}}, _NO_CONTENT_MESSAGE),
    ],
)
async def test_fastcrw_rejects_failed_or_empty_results(
    payload: dict[str, object],
    message: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(json=payload)
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await _provider(client).fetch_url(_ARTICLE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == message


async def test_fastcrw_discards_the_body_returned_with_a_missing_target() -> (
    None
):
    """A 404 answers HTTP 200 carrying an unrelated page in ``data``.

    Probing the live API for a missing path under ``example.com`` returned
    ``success: false`` alongside the markdown of the site root. Reading the
    content before the outcome is known would hand a caller the wrong page
    under the URL it asked for, so neither the success flag nor the status is
    allowed to be reached through the body.
    """
    missing_url = "https://example.test/definitely-missing"
    other_page = "# Some Other Page\n\n" + ("body " * 60)
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={
                "success": False,
                "data": {
                    "markdown": other_page,
                    "metadata": {"statusCode": 404},
                },
                "error": "Target returned 404 Not Found",
                "errorCode": "http_error",
            }
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await _provider(client).fetch_url(missing_url)

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert "Some Other Page" not in str(error_info.value)


async def test_fastcrw_reads_a_missing_target_out_of_the_error_message() -> (
    None
):
    """Not every miss carries a status, so the message is still consulted."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={
                "success": False,
                "error": "Target returned 404 Not Found",
                "errorCode": "http_error",
            }
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await _provider(client).fetch_url(
                    "https://example.test/definitely-missing"
                )

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert str(error_info.value) == (
        "fastCRW scrape failed: Target returned 404 Not Found"
    )


@pytest.mark.parametrize("status", sorted(_DEFINITELY_MISSING_STATUSES))
async def test_fastcrw_trusts_the_status_over_an_unrelated_error_string(
    status: int,
) -> None:
    """A real 404 arrived labelled ``lightpanda_budget_truncated``.

    Fetching a missing page under postgresql.org through the live API produced
    ``success: false`` with that renderer-internal string as ``error`` and
    ``statusCode: 404`` in the metadata. The message says nothing about the
    target, so judging by message alone would demote a definitive miss to a
    transient API error and spend the next provider on a page that does not
    exist. The status is read first for exactly this case.

    Parametrized over the whole definitive set rather than 404 alone: a 410 in
    the same costume is the identical defect, and the message pattern would not
    recognize one.
    """
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={
                "success": False,
                "data": {
                    "markdown": "# PostgreSQL: Not Found\n\n" + ("x " * 80),
                    "metadata": {"statusCode": status},
                },
                "error": "lightpanda_budget_truncated",
            }
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await _provider(client).fetch_url(
                    "https://example.test/missing"
                )

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert str(error_info.value) == (f"fastCRW target returned status {status}")


@pytest.mark.parametrize("status", sorted(_DEFINITELY_MISSING_STATUSES))
async def test_fastcrw_maps_target_status_to_not_found(status: int) -> None:
    """A success flag paired with a missing status is still a missing target."""
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            json={
                "success": True,
                "data": {
                    "markdown": "# Not the page you asked for",
                    "metadata": {"statusCode": status},
                },
            }
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await _provider(client).fetch_url(
                    "https://example.test/missing"
                )

    assert error_info.value.error_type is ErrorType.NOT_FOUND
    assert str(error_info.value) == (f"fastCRW target returned status {status}")


async def test_fastcrw_maps_rejected_credentials() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(_SCRAPE_URL).respond(
            401, json={"success": False, "error": "Invalid or missing API key"}
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderError) as error_info:
                await _provider(client).fetch_url(_ARTICLE_URL)

    assert error_info.value.error_type is ErrorType.API_ERROR
    assert str(error_info.value) == "Invalid API key"


def test_fastcrw_registers_and_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(fastcrw_module)

    assert fastcrw_module.FastcrwFetchProvider.name == "fastcrw"
    assert (
        fastcrw_module.FastcrwFetchProvider.base_url
        == "https://api.fastcrw.com"
    )
    assert fastcrw_module.FastcrwFetchProvider.required_secrets == (
        "CRW_API_KEY",
    )
    assert get_active_fetch_providers(ProviderSecrets({})) == []
    assert get_active_fetch_providers(
        ProviderSecrets({"CRW_API_KEY": "fastcrw-secret"})
    ) == ["fastcrw"]


async def test_fastcrw_runs_after_tavily_and_before_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The waterfall reaches fastCRW only once Tavily fails, and stops there.

    Firecrawl is configured but deliberately left unmocked. The attempt list
    is the load-bearing assertion: were fastCRW placed after Firecrawl, the
    race would attempt Firecrawl, record its unmocked-request failure, and
    reach fastCRW with a three-name attempt list.
    """
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(tavily_module)
    importlib.reload(fastcrw_module)
    importlib.reload(firecrawl_module)

    with respx.mock(assert_all_called=True) as router:
        router.post("https://api.tavily.com/extract").respond(
            json={
                "results": [],
                "failed_results": [
                    {"url": _ARTICLE_URL, "error": "upstream reset"}
                ],
            }
        )
        router.post(_SCRAPE_URL).respond(
            json={
                "success": True,
                "data": {"markdown": "# fastCRW\n\n" + ("real content " * 30)},
            }
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets(
                    {
                        "TAVILY_API_KEY": "tavily-secret",
                        "CRW_API_KEY": "fastcrw-secret",
                        "FIRECRAWL_API_KEY": "fire-secret",
                    }
                ),
                client,
            )
            result = await run_fetch_race(unified, _ARTICLE_URL)

    assert result.provider_used == "fastcrw"
    assert result.providers_attempted == ("tavily", "fastcrw")
    assert [failure.provider for failure in result.providers_failed] == [
        "tavily"
    ]


@pytest.mark.parametrize(
    "credential",
    ["self-secret", " self-secret ,other", '"self-secret,other"'],
)
async def test_selfhosted_uses_own_endpoint_and_first_key(
    credential: str,
) -> None:
    with respx.mock as router:
        route = router.post("https://crw.angrist.net/v1/scrape").respond(
            json={"success": True, "data": {"markdown": "# Document"}}
        )
        async with httpx.AsyncClient() as client:
            provider = SelfHostedFastcrwFetchProvider(
                ProviderSecrets({"CRW_AUTH__API_KEYS": credential}), client
            )
            result = await provider.fetch_url("https://example.test/file.pdf")
    assert (
        route.calls[0].request.headers["Authorization"] == "Bearer self-secret"
    )
    assert _json_request(route.calls[0].request)["url"] == (
        "https://example.test/file.pdf"
    )
    assert result.source_provider == "fastcrw_selfhosted"
    assert result.content == "# Document"


@pytest.mark.parametrize("credential", ["", " ", ",other"])
async def test_selfhosted_rejects_missing_first_key(credential: str) -> None:
    async with httpx.AsyncClient() as client:
        provider = SelfHostedFastcrwFetchProvider(
            ProviderSecrets({"CRW_AUTH__API_KEYS": credential}), client
        )
        with pytest.raises(ProviderError, match="API key"):
            await provider.fetch_url(_ARTICLE_URL)


@pytest.mark.parametrize("selfhosted_success", [True, False])
async def test_selfhosted_precedes_tavily_and_fails_over(
    monkeypatch: pytest.MonkeyPatch, selfhosted_success: bool
) -> None:
    monkeypatch.setattr(base, "_REGISTRY", {})
    importlib.reload(fastcrw_module)
    importlib.reload(selfhosted_module)
    importlib.reload(tavily_module)
    content = "# Article\n\n" + "Useful page content. " * 30
    with respx.mock(assert_all_called=False) as router:
        router.post("https://crw.angrist.net/v1/scrape").respond(
            json={"success": selfhosted_success, "data": {"markdown": content}}
        )
        tavily = router.post("https://api.tavily.com/extract").respond(
            json={"results": [{"url": _ARTICLE_URL, "raw_content": content}]}
        )
        async with httpx.AsyncClient() as client:
            unified = UnifiedFetchProvider(
                ProviderSecrets(
                    {
                        "CRW_AUTH__API_KEYS": "self-secret",
                        "TAVILY_API_KEY": "tavily-secret",
                    }
                ),
                client,
            )
            result = await run_fetch_race(unified, _ARTICLE_URL)
    expected = (
        ("fastcrw_selfhosted",)
        if selfhosted_success
        else ("fastcrw_selfhosted", "tavily")
    )
    assert result.providers_attempted == expected
    assert result.provider_used == expected[-1]
    assert tavily.called is not selfhosted_success


def test_selfhosted_requires_its_own_secret() -> None:
    assert not SelfHostedFastcrwFetchProvider.is_available(
        ProviderSecrets({"CRW_API_KEY": "hosted-secret"})
    )
    assert SelfHostedFastcrwFetchProvider.is_available(
        ProviderSecrets({"CRW_AUTH__API_KEYS": "self-secret"})
    )
