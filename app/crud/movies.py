from sqlalchemy import Table, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.movies import (
    Certification,
    Director,
    Genre,
    Movie,
    Star,
    movie_directors,
    movie_genres,
    movie_stars,
)
from app.schemas.movies import MovieCreate, MovieUpdate

# Link table, its column for the linked item, the request field with ids.
LINKS = (
    (movie_genres, "genre_id", "genre_ids"),
    (movie_stars, "star_id", "star_ids"),
    (movie_directors, "director_id", "director_ids"),
)
LIST_FIELDS = tuple(field for _, _, field in LINKS)

# Everything the movie response shows, loaded right away: lazy loading in
# async code fails with MissingGreenlet -> 500.
MOVIE_DETAILS = (
    joinedload(Movie.certification),
    selectinload(Movie.genres),
    selectinload(Movie.stars),
    selectinload(Movie.directors),
)


async def get_movie(db: AsyncSession, movie_id: int) -> Movie | None:
    return await db.scalar(
        select(Movie).where(Movie.id == movie_id).options(*MOVIE_DETAILS)
    )


async def _add_links(
    db: AsyncSession, movie_id: int, table: Table, column: str, ids: list[int]
) -> None:
    # Core insert, not `movie.genres = [...]`: the ORM would load the items
    # by id and silently drop unknown ones, the DB foreign key rejects them.
    if ids:
        await db.execute(
            insert(table), [{"movie_id": movie_id, column: id_} for id_ in ids]
        )


async def create_movie(db: AsyncSession, data: MovieCreate) -> int:
    """Nothing is checked beforehand: the DB decides (unique name + year +
    time, existing ids), so a parallel request can't slip past a check.
    A violation raises IntegrityError and nothing is saved.
    """
    movie_id = await db.scalar(
        insert(Movie)
        .values(**data.model_dump(exclude=set(LIST_FIELDS)))
        .returning(Movie.id)
    )
    for table, column, field in LINKS:
        await _add_links(db, movie_id, table, column, getattr(data, field))
    await db.commit()
    return movie_id


async def update_movie(db: AsyncSession, movie_id: int, data: MovieUpdate) -> bool:
    """Returns False if the movie does not exist.

    The movie row is locked first, so a parallel update of the same movie
    waits: two requests can't mix their genre lists, and an update after
    a parallel delete finds nothing. Only the id is selected, so the
    session holds no Movie object that could show the old data later.
    A sent list replaces the old one. Like create, the DB checks the ids
    and the unique name + year + time (IntegrityError, nothing saved).
    """
    locked_id = await db.scalar(
        select(Movie.id).where(Movie.id == movie_id).with_for_update()
    )
    if locked_id is None:
        return False
    changes = data.model_dump(exclude_unset=True, exclude=set(LIST_FIELDS))
    if changes:
        await db.execute(update(Movie).where(Movie.id == movie_id).values(**changes))
    for table, column, field in LINKS:
        if field in data.model_fields_set:
            await db.execute(delete(table).where(table.c.movie_id == movie_id))
            await _add_links(db, movie_id, table, column, getattr(data, field))
    await db.commit()
    return True


async def delete_movie(db: AsyncSession, movie_id: int) -> bool:
    """Returns False if the movie does not exist. Its links go by CASCADE."""
    deleted_id = await db.scalar(
        delete(Movie).where(Movie.id == movie_id).returning(Movie.id)
    )
    await db.commit()
    return deleted_id is not None


async def find_missing_ids(
    db: AsyncSession, data: MovieCreate | MovieUpdate
) -> dict[str, list[int]]:
    """Which of the sent ids don't exist, e.g. {"genre_ids": [99]}.

    Called only after the DB has rejected the movie, to tell the
    moderator exactly what to fix. Fields not sent in an update are None.
    """
    certification_ids = (
        [data.certification_id] if data.certification_id is not None else []
    )
    checks = (
        ("certification_id", Certification, certification_ids),
        ("genre_ids", Genre, data.genre_ids),
        ("star_ids", Star, data.star_ids),
        ("director_ids", Director, data.director_ids),
    )
    missing = {}
    for field, model, ids in checks:
        if not ids:
            continue
        found = set(await db.scalars(select(model.id).where(model.id.in_(ids))))
        not_found = [id_ for id_ in ids if id_ not in found]
        if not_found:
            missing[field] = not_found
    return missing
