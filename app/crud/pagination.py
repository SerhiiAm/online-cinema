from sqlalchemy import Select

from app.schemas.pagination import PAGE_SIZE


def page_of(statement: Select, page: int) -> Select:
    """Only the rows of the given page (numbered from 1)."""
    return statement.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
