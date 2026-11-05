from sqlalchemy import insert, select
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
from app.schemas.movies import MovieCreate

LIST_FIELDS = ("genre_ids", "star_ids", "director_ids")

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
    links = (
        (movie_genres, "genre_id", data.genre_ids),
        (movie_stars, "star_id", data.star_ids),
        (movie_directors, "director_id", data.director_ids),
    )
    for table, column, ids in links:
        if ids:
            await db.execute(
                insert(table), [{"movie_id": movie_id, column: id_} for id_ in ids]
            )
    await db.commit()
    return movie_id


async def find_missing_ids(db: AsyncSession, data: MovieCreate) -> dict[str, list[int]]:
    """Which of the sent ids don't exist, e.g. {"genre_ids": [99]}.

    Called only after the DB has rejected the movie, to tell the
    moderator exactly what to fix.
    """
    checks = (
        ("certification_id", Certification, [data.certification_id]),
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
