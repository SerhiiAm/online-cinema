"""Shared helpers for the movie and reference tests."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

ReferenceItem = Genre | Star | Director | Certification


@dataclass(frozen=True)
class ReferenceCase:
    path: str
    title: str
    model: type[ReferenceItem]


REFERENCE_CASES = [
    ReferenceCase("/genres", "Genre", Genre),
    ReferenceCase("/stars", "Star", Star),
    ReferenceCase("/directors", "Director", Director),
    ReferenceCase("/certifications", "Certification", Certification),
]

# Which Movie attribute holds a list of items of this model.
_MOVIE_LISTS = {Genre: "genres", Star: "stars", Director: "directors"}


def auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def add_item(
    db: AsyncSession, model: type[ReferenceItem], name: str
) -> ReferenceItem:
    item = model(name=name)
    db.add(item)
    await db.commit()
    return item


async def add_movie(db: AsyncSession, name: str, item: ReferenceItem) -> Movie:
    """Saves a movie that uses the given genre/star/director/certification."""
    if isinstance(item, Certification):
        certification = item
    else:
        certification = Certification(name=f"Certification for {name}")
    movie = Movie(
        name=name,
        year=2000,
        time=120,
        imdb=7.5,
        votes=1000,
        description="A movie.",
        price=Decimal("9.99"),
        certification=certification,
    )
    if not isinstance(item, Certification):
        setattr(movie, _MOVIE_LISTS[type(item)], [item])
    db.add(movie)
    await db.commit()
    return movie


async def get_names(db: AsyncSession, model: type[ReferenceItem]) -> list[str]:
    return list(await db.scalars(select(model.name).order_by(model.id)))


def movie_body(refs: dict[str, int], **changes: Any) -> dict[str, Any]:
    """A valid POST /movies body built from the `refs` fixture ids."""
    body = {
        "name": "Inception",
        "year": 2010,
        "time": 148,
        "imdb": 8.8,
        "votes": 2_000_000,
        "description": "A thief who steals secrets through dreams.",
        "price": "9.99",
        "certification_id": refs["certification"],
        "genre_ids": [refs["drama"]],
        "director_ids": [refs["director"]],
    }
    return body | changes


async def count_rows(db: AsyncSession) -> dict[str, int]:
    """Rows in movies and in the three link tables."""
    await db.rollback()  # see what other sessions committed
    tables = {
        "movies": Movie.__table__,
        "genres": movie_genres,
        "stars": movie_stars,
        "directors": movie_directors,
    }
    return {
        name: await db.scalar(select(func.count()).select_from(table))
        for name, table in tables.items()
    }


NOTHING_SAVED = {"movies": 0, "genres": 0, "stars": 0, "directors": 0}
