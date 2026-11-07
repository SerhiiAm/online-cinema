from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import ModeratorUser, PathId
from app.crud import movies as crud
from app.schemas.movies import (
    MovieCreate,
    MovieListItem,
    MovieListQuery,
    MovieResponse,
    MovieUpdate,
)
from app.schemas.pagination import PAGE_SIZE, Page

router = APIRouter(prefix="/movies", tags=["Movies"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# PostgreSQL error codes (`sqlstate`).
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"

MOVIE_EXISTS = "A movie with this name, year and time already exists."
UNKNOWN_IDS = "These ids don't exist: {ids}."
MOVIE_NOT_FOUND = "Movie not found."

AUTH_ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {
        "description": "The access token is missing, invalid or expired."
    },
    status.HTTP_403_FORBIDDEN: {
        "description": "You are not a moderator or an admin, "
        "or the account is inactive."
    },
}
WRITE_ERRORS = {
    **AUTH_ERRORS,
    status.HTTP_400_BAD_REQUEST: {
        "description": "Some of the ids don't exist (listed in the message)."
    },
    status.HTTP_409_CONFLICT: {"description": MOVIE_EXISTS},
}
NOT_FOUND_ERROR = {status.HTTP_404_NOT_FOUND: {"description": MOVIE_NOT_FOUND}}

LIST_RULES = (
    "The certification, genres, stars and directors are passed by id: "
    "create them first in their own sections. At least one genre and one "
    "director are required, stars are optional. Repeated ids in a list are "
    "counted once."
)


def _format_missing(missing: dict[str, list[int]]) -> str:
    return ", ".join(f"{field} {ids}" for field, ids in missing.items())


async def _raise_write_error(
    db: AsyncSession, exc: IntegrityError, data: MovieCreate | MovieUpdate
) -> NoReturn:
    """Turns a DB rejection of a created or updated movie into 409 / 400."""
    # The failed transaction must be closed before any further query.
    await db.rollback()
    # The same code can mean different things, so check which one.
    if exc.orig.sqlstate == UNIQUE_VIOLATION:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=MOVIE_EXISTS)
    if exc.orig.sqlstate == FOREIGN_KEY_VIOLATION:
        missing = await crud.find_missing_ids(db, data)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=UNKNOWN_IDS.format(ids=_format_missing(missing)),
        )
    raise exc


async def _movie_response(db: AsyncSession, movie_id: int) -> MovieResponse:
    movie = await crud.get_movie(db, movie_id)
    if movie is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=MOVIE_NOT_FOUND
        )
    return MovieResponse.model_validate(movie)


@router.get(
    "",
    summary="Browse the movie catalog",
    description=(
        f"{PAGE_SIZE} movies per page; `total` counts the movies on all "
        "pages. A page after the last one is empty. `search` looks for the "
        "text as a whole (`dark knight`, not `knight dark`); an empty one "
        "shows everything. Search and filters can be combined; "
        "the year bounds and `imdb_min` are included. Sort by `year`, "
        "`price`, `imdb` or `votes` (popularity); `-` in front means "
        "descending. Newest first by default. No login needed."
    ),
)
async def list_movies(
    query: Annotated[MovieListQuery, Query()], db: DbSession
) -> Page[MovieListItem]:
    movies, total = await crud.list_movies(db, query)
    return Page[MovieListItem].model_validate(
        {"items": movies, "total": total, "page": query.page}, from_attributes=True
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a movie (moderator or admin)",
    description=(
        f"{LIST_RULES} Spaces around the name are removed. The price must be "
        "above 0 with at most 2 decimal places."
    ),
    responses=WRITE_ERRORS,
)
async def create_movie(
    data: MovieCreate, moderator: ModeratorUser, db: DbSession
) -> MovieResponse:
    try:
        movie_id = await crud.create_movie(db, data)
    except IntegrityError as exc:
        await _raise_write_error(db, exc, data)
    return await _movie_response(db, movie_id)


@router.get(
    "/{movie_id}",
    summary="Get a movie",
    description=(
        "All the movie's data with its certification, genres, stars and "
        "directors. No login needed."
    ),
    responses=NOT_FOUND_ERROR,
)
async def get_movie(movie_id: PathId, db: DbSession) -> MovieResponse:
    return await _movie_response(db, movie_id)


@router.patch(
    "/{movie_id}",
    summary="Update a movie (moderator or admin)",
    description=(
        "Send only the fields to change; the rules are the same as on "
        "create. A sent list replaces the old one: `star_ids: []` removes "
        "all stars. Only `meta_score` and `gross` can be cleared with null. "
        f"{LIST_RULES} An empty body changes nothing."
    ),
    responses={**WRITE_ERRORS, **NOT_FOUND_ERROR},
)
async def update_movie(
    movie_id: PathId, data: MovieUpdate, moderator: ModeratorUser, db: DbSession
) -> MovieResponse:
    try:
        updated = await crud.update_movie(db, movie_id, data)
    except IntegrityError as exc:
        await _raise_write_error(db, exc, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=MOVIE_NOT_FOUND
        )
    return await _movie_response(db, movie_id)


@router.delete(
    "/{movie_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a movie (moderator or admin)",
    description=(
        "Removes the movie together with its links to genres, stars and "
        "directors; the genres, stars and directors themselves stay."
    ),
    responses={**AUTH_ERRORS, **NOT_FOUND_ERROR},
)
async def delete_movie(
    movie_id: PathId, moderator: ModeratorUser, db: DbSession
) -> None:
    # Orders (feature 6) will make a purchased movie undeletable: the DB
    # will refuse (RESTRICT) and the IntegrityError will be mapped to 409 here.
    if not await crud.delete_movie(db, movie_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=MOVIE_NOT_FOUND
        )
