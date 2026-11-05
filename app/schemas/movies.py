from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# Spaces around the name are cut off, so " Drama" can't become a second
# "Drama"; max_length matches String(255) in the DB (a longer name would
# be a DB error -> 500 instead of a 422).
ReferenceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class ReferenceRequest(BaseModel):
    name: ReferenceName


class ReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    movie_count: int
