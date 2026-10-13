"""Shared public input bounds (SQLite identifiers are signed 64-bit values)."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt

MAX_INTEGER = 2**63 - 1
Identifier = Annotated[int, Field(ge=1, le=MAX_INTEGER)]
BodyIdentifier = Annotated[StrictInt, Field(ge=1, le=MAX_INTEGER)]
Offset = Annotated[int, Field(ge=0, le=MAX_INTEGER)]
ImageLimit = Annotated[int, Field(ge=0, le=10000)]
Sort = Literal['newest', 'oldest', 'path', 'rating', 'avg', 'random']
