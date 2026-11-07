"""The page format shared by every paginated list."""

from typing import Annotated

from pydantic import BaseModel, Field

from app.core.dependencies import MAX_DB_ID

PAGE_SIZE = 10
# A bigger number would be rejected by the DB driver -> 500.
PageNumber = Annotated[int, Field(ge=1, le=MAX_DB_ID)]


class Page[ItemT](BaseModel):
    """`total` counts the items on all pages; a page after the last is empty."""

    items: list[ItemT]
    total: int
    page: int
