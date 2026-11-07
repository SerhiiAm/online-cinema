"""CRUD for genres, stars, directors and certifications.

Every function gets the model, and the ones that count movies also get
`movie_column`: the column that points from a movie to the item
(movie_genres.genre_id for genres, movies.certification_id for
certifications, ...).
"""

from sqlalchemy import Column, Row, Select, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.crud.pagination import page_of
from app.models.movies import Certification, Director, Genre, Star

ReferenceModel = type[Genre] | type[Star] | type[Director] | type[Certification]
MovieColumn = Column[int] | InstrumentedAttribute[int]


def _count_movies(movie_column: MovieColumn, item_id: object) -> Select:
    return select(func.count()).where(movie_column == item_id)


def _columns_with_movie_count(
    model: ReferenceModel, movie_column: MovieColumn
) -> tuple:
    # A subquery inside the same query instead of a query per item.
    movie_count = _count_movies(movie_column, model.id).scalar_subquery()
    return model.id, model.name, movie_count.label("movie_count")


# Names are unique, so the order is stable without an id tiebreak.
def _sorted_by_name(model: ReferenceModel, movie_column: MovieColumn) -> Select:
    return select(*_columns_with_movie_count(model, movie_column)).order_by(
        model.name
    )


async def list_references(
    db: AsyncSession, model: ReferenceModel, movie_column: MovieColumn
) -> list[Row]:
    result = await db.execute(_sorted_by_name(model, movie_column))
    return list(result.all())


async def list_references_page(
    db: AsyncSession, model: ReferenceModel, movie_column: MovieColumn, page: int
) -> tuple[list[Row], int]:
    """One page and the number of items on all pages. Movies are counted
    only for the items of the page."""
    total = await db.scalar(select(func.count()).select_from(model))
    result = await db.execute(page_of(_sorted_by_name(model, movie_column), page))
    return list(result.all()), total


async def count_movies(
    db: AsyncSession, movie_column: MovieColumn, item_id: int
) -> int:
    return await db.scalar(_count_movies(movie_column, item_id))


# The writes below don't check anything beforehand: the DB decides
# (unique name, RESTRICT on delete), so two parallel requests can't both
# pass a check. A violation raises IntegrityError, the router maps it.


async def create_reference(db: AsyncSession, model: ReferenceModel, name: str) -> int:
    item_id = await db.scalar(insert(model).values(name=name).returning(model.id))
    await db.commit()
    return item_id


async def rename_reference(
    db: AsyncSession,
    model: ReferenceModel,
    movie_column: MovieColumn,
    item_id: int,
    name: str,
) -> Row | None:
    """Returns None if the item does not exist.

    The movie count comes back in the same statement, so a delete made
    in parallel can't slip in between the rename and the read.
    """
    result = await db.execute(
        update(model)
        .where(model.id == item_id)
        .values(name=name)
        .returning(*_columns_with_movie_count(model, movie_column))
    )
    row = result.one_or_none()
    await db.commit()
    return row


async def delete_reference(
    db: AsyncSession, model: ReferenceModel, item_id: int
) -> bool:
    """Returns False if the item does not exist."""
    deleted_id = await db.scalar(
        delete(model).where(model.id == item_id).returning(model.id)
    )
    await db.commit()
    return deleted_id is not None
