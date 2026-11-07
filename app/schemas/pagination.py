"""The page format and the search text shared by the paginated lists."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.core.dependencies import MAX_DB_ID

PAGE_SIZE = 10
# A bigger number would be rejected by the DB driver -> 500.
PageNumber = Annotated[int, Field(ge=1, le=MAX_DB_ID)]
# Spaces around are cut; an empty search means "no search", like an empty
# search box on any site. Names are at most 255 long, so a longer text
# could only match a description and would just make the DB work harder.
SearchText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=255)
]


class Page[ItemT](BaseModel):
    """`total` counts the items on all pages; a page after the last is empty."""

    items: list[ItemT]
    total: int
    page: int
