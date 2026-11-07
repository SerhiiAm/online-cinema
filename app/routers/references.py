"""Genres, stars, directors and certifications.

All four have the same endpoints, so one factory builds them: a fix made
here applies to all four. Reading is public, changes need a moderator
(or an admin).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import ModeratorUser, PathId
from app.crud import references as crud
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
from app.schemas.movies import ReferenceRequest, ReferenceResponse
from app.schemas.pagination import PAGE_SIZE, Page, PageNumber

DbSession = Annotated[AsyncSession, Depends(get_db)]

NAME_TAKEN = "{title} with this name already exists."
NOT_FOUND = "{title} not found."
IN_USE = "{title} is used by {count} movie(s) and can't be deleted."


def build_reference_router(
    model: crud.ReferenceModel,
    movie_column: crud.MovieColumn,
    prefix: str,
    title: str,
    paginated: bool = False,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[f"{title}s"])
    plural = f"{title.lower()}s"
    name_taken = NAME_TAKEN.format(title=title)
    not_found = NOT_FOUND.format(title=title)

    auth_errors = {
        status.HTTP_401_UNAUTHORIZED: {
            "description": "The access token is missing, invalid or expired."
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "You are not a moderator or an admin, "
            "or the account is inactive."
        },
    }
    name_rules = (
        "The name is unique ignoring case (`Drama` and `drama` are the same) "
        "and spaces around it are removed."
    )

    list_description = (
        f"{plural.capitalize()} sorted by name, each with the number of "
        "movies it is used in. No login needed."
    )
    if paginated:

        @router.get(
            "",
            summary=f"List {plural} page by page",
            description=f"{list_description} {PAGE_SIZE} per page; `total` "
            "counts all of them. A page after the last one is empty.",
        )
        async def list_items_page(
            db: DbSession, page: Annotated[PageNumber, Query()] = 1
        ) -> Page[ReferenceResponse]:
            rows, total = await crud.list_references_page(
                db, model, movie_column, page
            )
            return Page[ReferenceResponse].model_validate(
                {"items": rows, "total": total, "page": page}, from_attributes=True
            )

    else:

        @router.get("", summary=f"List {plural}", description=list_description)
        async def list_items(db: DbSession) -> list[ReferenceResponse]:
            rows = await crud.list_references(db, model, movie_column)
            return [ReferenceResponse.model_validate(row) for row in rows]

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        summary=f"Create a {title.lower()} (moderator or admin)",
        description=name_rules,
        responses={
            **auth_errors,
            status.HTTP_409_CONFLICT: {"description": name_taken},
        },
    )
    async def create_item(
        data: ReferenceRequest, moderator: ModeratorUser, db: DbSession
    ) -> ReferenceResponse:
        try:
            item_id = await crud.create_reference(db, model, data.name)
        except IntegrityError:
            # The only constraint an insert here can break is the unique name.
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=name_taken)
        return ReferenceResponse(id=item_id, name=data.name, movie_count=0)

    @router.patch(
        "/{item_id}",
        summary=f"Rename a {title.lower()} (moderator or admin)",
        description=f"{name_rules} Movies using it show the new name at once.",
        responses={
            **auth_errors,
            status.HTTP_404_NOT_FOUND: {"description": not_found},
            status.HTTP_409_CONFLICT: {"description": name_taken},
        },
    )
    async def rename_item(
        item_id: PathId,
        data: ReferenceRequest,
        moderator: ModeratorUser,
        db: DbSession,
    ) -> ReferenceResponse:
        try:
            row = await crud.rename_reference(
                db, model, movie_column, item_id, data.name
            )
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=name_taken)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found)
        return ReferenceResponse.model_validate(row)

    @router.delete(
        "/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary=f"Delete a {title.lower()} (moderator or admin)",
        description=(
            f"Only a {title.lower()} that no movie uses can be deleted: "
            "change those movies first. This way a mistaken delete can't "
            "silently strip it from hundreds of movies."
        ),
        responses={
            **auth_errors,
            status.HTTP_404_NOT_FOUND: {"description": not_found},
            status.HTTP_409_CONFLICT: {
                "description": f"The {title.lower()} is used by movies."
            },
        },
    )
    async def delete_item(
        item_id: PathId, moderator: ModeratorUser, db: DbSession
    ) -> None:
        try:
            deleted = await crud.delete_reference(db, model, item_id)
        except IntegrityError:
            # RESTRICT in the DB: a movie still points to this item.
            await db.rollback()
            count = await crud.count_movies(db, movie_column, item_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=IN_USE.format(title=title, count=count),
            )
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found)

    return router


# Top-level paths, not /movies/genres: there "genres" would be taken
# for a movie id in /movies/{id} -> 422.
genres_router = build_reference_router(
    Genre, movie_genres.c.genre_id, "/genres", "Genre"
)
# A catalog has thousands of stars and directors but a few dozen genres
# and certifications, so only the first two come page by page.
stars_router = build_reference_router(
    Star, movie_stars.c.star_id, "/stars", "Star", paginated=True
)
directors_router = build_reference_router(
    Director, movie_directors.c.director_id, "/directors", "Director", paginated=True
)
certifications_router = build_reference_router(
    Certification, Movie.certification_id, "/certifications", "Certification"
)

routers = [genres_router, stars_router, directors_router, certifications_router]
