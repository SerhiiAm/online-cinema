from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import ModeratorUser
from app.crud import movies as crud
from app.schemas.movies import MovieCreate, MovieResponse

router = APIRouter(prefix="/movies", tags=["Movies"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# PostgreSQL error codes (`sqlstate`).
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"

MOVIE_EXISTS = "A movie with this name, year and time already exists."
UNKNOWN_IDS = "These ids don't exist: {ids}."

AUTH_ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {
        "description": "The access token is missing, invalid or expired."
    },
    status.HTTP_403_FORBIDDEN: {
        "description": "You are not a moderator or an admin, "
        "or the account is inactive."
    },
}


def _format_missing(missing: dict[str, list[int]]) -> str:
    return ", ".join(f"{field} {ids}" for field, ids in missing.items())


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a movie (moderator or admin)",
    description=(
        "The certification, genres, stars and directors are passed by id: "
        "create them first in their own sections. Repeated ids in a list "
        "are counted once, spaces around the name are removed. The price "
        "must be above 0 with at most 2 decimal places."
    ),
    responses={
        **AUTH_ERRORS,
        status.HTTP_400_BAD_REQUEST: {
            "description": "Some of the ids don't exist (listed in the message)."
        },
        status.HTTP_409_CONFLICT: {"description": MOVIE_EXISTS},
    },
)
async def create_movie(
    data: MovieCreate, moderator: ModeratorUser, db: DbSession
) -> MovieResponse:
    try:
        movie_id = await crud.create_movie(db, data)
    except IntegrityError as exc:
        await db.rollback()
        # The same code can mean different things, so check which one.
        if exc.orig.sqlstate == UNIQUE_VIOLATION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=MOVIE_EXISTS
            )
        if exc.orig.sqlstate == FOREIGN_KEY_VIOLATION:
            missing = await crud.find_missing_ids(db, data)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=UNKNOWN_IDS.format(ids=_format_missing(missing)),
            )
        raise
    return MovieResponse.model_validate(await crud.get_movie(db, movie_id))
