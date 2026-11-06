from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.core.dependencies import MAX_DB_ID

# Spaces around the name are cut off, so " Drama" can't become a second
# "Drama"; max_length matches String(255) in the DB (a longer name would
# be a DB error -> 500 instead of a 422). Movie names use the same rule.
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


# --- Movies ---

FIRST_MOVIE_YEAR = 1888
# Announced movies may be added a few years ahead; a bigger year is a typo
# (2205 instead of 2025) that would stay on top of the newest-first catalog.
MAX_YEARS_AHEAD = 5


def _not_too_far_ahead(year: int) -> int:
    # Checked on every request, so the limit moves with the calendar.
    max_year = date.today().year + MAX_YEARS_AHEAD
    if year > max_year:
        raise ValueError(f"Year must be {max_year} or earlier.")
    return year


def _without_duplicates(ids: list[int]) -> list[int]:
    # [1, 1] would insert the same link twice -> a DB error that looks
    # exactly like "the movie already exists".
    return list(dict.fromkeys(ids))


# An id bigger than a PostgreSQL integer makes the DB driver fail -> 500.
DbId = Annotated[int, Field(ge=1, le=MAX_DB_ID)]
DbInt = Annotated[int, Field(le=MAX_DB_ID)]
# A movie never has that many genres/stars/directors; a huge list would
# also break the DB driver (at most 32767 query arguments) -> 500.
MAX_LIST_IDS = 50
IdList = Annotated[
    list[DbId], Field(max_length=MAX_LIST_IDS), AfterValidator(_without_duplicates)
]
# Every movie has a genre and a director (a nature documentary may have
# no stars): without them it would be missing from the genre filter and
# the director search.
RequiredIdList = Annotated[IdList, Field(min_length=1)]
# JSON allows Infinity and NaN, and Infinity would pass ge=0 and be saved.
Number = Annotated[float, Field(allow_inf_nan=False)]
MovieYear = Annotated[
    int,
    Field(
        ge=FIRST_MOVIE_YEAR,
        description=f"From {FIRST_MOVIE_YEAR} to the current year "
        f"+ {MAX_YEARS_AHEAD}.",
    ),
    AfterValidator(_not_too_far_ahead),
]
# decimal_places=2: the DB would silently round 9.999 to 10.00, so the
# customer would pay a price nobody entered. max_digits=10 = Numeric(10, 2).
Price = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Shared by create and update, so an update can't bypass a rule.
Duration = Annotated[DbInt, Field(ge=1, description="Duration in minutes.")]
Imdb = Annotated[Number, Field(ge=0, le=10, description="IMDb rating, 0-10.")]
Votes = Annotated[DbInt, Field(ge=0, description="Number of IMDb votes.")]
MetaScore = Annotated[
    Number, Field(ge=0, le=100, description="Metacritic score, 0-100.")
]
Gross = Annotated[Number, Field(ge=0, description="Box office revenue in US dollars.")]


class MovieCreate(BaseModel):
    name: ReferenceName
    year: MovieYear
    time: Duration
    imdb: Imdb
    votes: Votes
    meta_score: MetaScore | None = None
    gross: Gross | None = None
    description: Description
    price: Price
    certification_id: DbId
    genre_ids: RequiredIdList
    star_ids: IdList = []
    director_ids: RequiredIdList


class MovieUpdate(BaseModel):
    """Only the sent fields change.

    The `None` defaults are not validated by Pydantic: an omitted field
    passes and stays unchanged, while an explicit null fails the type
    check -> 422 (the DB requires a value). Only meta_score and gross
    can be cleared with null.
    """

    name: ReferenceName = None
    year: MovieYear = None
    time: Duration = None
    imdb: Imdb = None
    votes: Votes = None
    meta_score: MetaScore | None = None
    gross: Gross | None = None
    description: Description = None
    price: Price = None
    certification_id: DbId = None
    genre_ids: RequiredIdList = None
    star_ids: IdList = None
    director_ids: RequiredIdList = None


class NamedItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class MovieResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: UUID
    name: str
    year: int
    time: int
    imdb: float
    votes: int
    meta_score: float | None
    gross: float | None
    description: str
    price: Decimal
    certification: NamedItem
    genres: list[NamedItem]
    stars: list[NamedItem]
    directors: list[NamedItem]
