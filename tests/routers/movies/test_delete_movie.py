"""DELETE /movies/{movie_id}."""

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID, PERMISSION_DENIED
from app.models.movies import Director, Genre, Star
from app.routers.movies import MOVIE_NOT_FOUND
from tests.routers.movies.helpers import NOTHING_SAVED, auth, count_rows, get_names


async def test_moderator_deletes_movie_with_its_links(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    movie: dict[str, Any],
) -> None:
    response = await client.delete(
        f"/movies/{movie['id']}", headers=auth(moderator_token)
    )

    assert response.status_code == 204
    assert await count_rows(db_session) == NOTHING_SAVED
    # Only the links are gone, the items themselves stay.
    assert await get_names(db_session, Genre) == ["Drama", "Action"]
    assert await get_names(db_session, Star) == ["Leonardo DiCaprio"]
    assert await get_names(db_session, Director) == ["Christopher Nolan"]
    response = await client.get(f"/movies/{movie['id']}")
    assert response.status_code == 404


async def test_unknown_movie_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    movie: dict[str, Any],
) -> None:
    response = await client.delete(
        f"/movies/{movie['id'] + 1}", headers=auth(moderator_token)
    )

    assert response.status_code == 404
    assert response.json() == {"detail": MOVIE_NOT_FOUND}
    assert (await count_rows(db_session))["movies"] == 1


async def test_plain_user_cannot_delete_movie(
    client: AsyncClient,
    db_session: AsyncSession,
    demoted_token: str,
    movie: dict[str, Any],
) -> None:
    response = await client.delete(
        f"/movies/{movie['id']}", headers=auth(demoted_token)
    )

    assert response.status_code == 403
    assert response.json() == {"detail": PERMISSION_DENIED}
    assert (await count_rows(db_session))["movies"] == 1


async def test_id_out_of_db_range_returns_422(
    client: AsyncClient, moderator_token: str
) -> None:
    response = await client.delete(
        f"/movies/{MAX_DB_ID + 1}", headers=auth(moderator_token)
    )

    assert response.status_code == 422
