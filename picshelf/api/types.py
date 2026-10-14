"""Shared public input bounds (SQLite identifiers are signed 64-bit values)."""
from typing import Annotated, Literal

from pydantic import Field, StrictInt
from ..inputs import MAX_INTEGER, MAX_IMAGE_LIMIT

Identifier = Annotated[int, Field(ge=1, le=MAX_INTEGER)]
BodyIdentifier = Annotated[StrictInt, Field(ge=1, le=MAX_INTEGER)]
Offset = Annotated[int, Field(ge=0, le=MAX_INTEGER)]
ImageLimit = Annotated[int, Field(ge=0, le=MAX_IMAGE_LIMIT)]
Sort = Literal['newest', 'oldest', 'path', 'rating', 'avg', 'random']
