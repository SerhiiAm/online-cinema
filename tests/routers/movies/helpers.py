"""Shared helpers for the movie and reference tests."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.movies import Certification, Director, Genre, Movie, Star

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
