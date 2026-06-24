"""Public tool input/output schemas.

Pydantic models and annotated types that FastMCP compiles into enforced JSON
Schemas: the input parameter contract (``GreetableName``) and the structured
output contract (``HelloResponse``). Defining them here keeps the wire contracts
in one place, decoupled from tool behavior.
"""

from __future__ import annotations

from typing import Annotated

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

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: Annotated[
        str,
        Field(description="The rendered greeting.", examples=["Hello, World!"]),
    ]
