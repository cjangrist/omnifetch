"""Public tool input/output schemas.

Pydantic models and annotated types that FastMCP compiles into enforced JSON
Schemas. Defining them here keeps the wire contracts in one place, decoupled
from tool behavior.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GreetableName = Annotated[
    str,
    Field(
        description="Name of the entity to greet.",
        min_length=1,
        max_length=100,
        examples=["World", "Ada Lovelace"],
    ),
]


class HelloResponse(BaseModel):
    """Structured greeting returned by the ``say_hello`` tool."""

    model_config = ConfigDict(extra="forbid")

    message: Annotated[
        str,
        Field(description="The rendered greeting.", examples=["Hello, World!"]),
    ]


FetchUrl = Annotated[
    str,
    Field(
        description="The URL to fetch - any public URL.",
        min_length=1,
        max_length=2000,
        examples=["https://example.com/article"],
    ),
]

SkipProviders = Annotated[
    str | list[str] | None,
    Field(
        description=(
            "Provider names to skip in the waterfall. Accepts a "
            "comma-separated string, JSON-encoded array string, or native "
            "array. Use only when a prior fetch returned wrong content."
        ),
        examples=["tavily", ["tavily", "firecrawl"]],
    ),
]


class FetchProviderFailure(BaseModel):
    """One provider failure recorded while fetching a URL."""

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[str, Field(description="Provider name.")]
    error: Annotated[str, Field(description="Provider error message.")]
    duration_ms: Annotated[
        float,
        Field(description="Provider attempt duration in milliseconds.", ge=0),
    ]
    error_type: Annotated[
        str,
        Field(description="Stable provider failure category."),
    ] = "PROVIDER_ERROR"


class FetchAlternative(BaseModel):
    """One non-primary successful fetch result."""

    model_config = ConfigDict(extra="forbid")

    url: Annotated[str, Field(description="Resolved URL for this result.")]
    title: Annotated[str, Field(description="Extracted page title.")]
    content: Annotated[str, Field(description="Fetched markdown content.")]
    source_provider: Annotated[
        str,
        Field(description="Provider that returned this result."),
    ]
    metadata: Annotated[
        dict[str, Any] | None,
        Field(description="Provider-specific metadata."),
    ] = None


class FetchResponse(BaseModel):
    """Structured result returned by the ``web_fetch`` tool."""

    model_config = ConfigDict(extra="forbid")

    status: Annotated[
        Literal["success", "not_found", "unavailable"],
        Field(
            description=(
                "Outcome of the fetch. Only success carries page content; "
                "not_found requires unanimous missing evidence or at least "
                "two independent providers, while unavailable is "
                "inconclusive exhaustion."
            )
        ),
    ] = "success"
    url: Annotated[str, Field(description="Resolved URL for the result.")]
    title: Annotated[str, Field(description="Extracted page title.")]
    content: Annotated[str, Field(description="Fetched markdown content.")]
    source_provider: Annotated[
        str,
        Field(description="Provider selected as the primary result."),
    ]
    total_duration_ms: Annotated[
        float,
        Field(description="Total fetch duration in milliseconds.", ge=0),
    ]
    metadata: Annotated[
        dict[str, Any] | None,
        Field(description="Provider-specific metadata."),
    ] = None
    providers_attempted: Annotated[
        list[str] | None,
        Field(description="Provider names tried in attempt order."),
    ] = None
    providers_failed: Annotated[
        list[FetchProviderFailure] | None,
        Field(description="Providers that failed during the waterfall."),
    ] = None
    alternative_results: Annotated[
        list[FetchAlternative] | None,
        Field(description="Additional successful provider results."),
    ] = None
    message: Annotated[
        str | None,
        Field(description="Human-readable detail for a non-success outcome."),
    ] = None
