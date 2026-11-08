"""GET /movies/{movie_id}."""

from typing import Any

from httpx import AsyncClient

from app.core.dependencies import MAX_DB_ID
from app.routers.movies import MOVIE_NOT_FOUND


async def test_anyone_gets_movie(client: AsyncClient, movie: dict[str, Any]) -> None:
    response = await client.get(f"/movies/{movie['id']}")  # no token

    assert response.status_code == 200
    assert response.json() == movie


async def test_unknown_movie_returns_404(
    client: AsyncClient, movie: dict[str, Any]
) -> None:
    response = await client.get(f"/movies/{movie['id'] + 1}")

    assert response.status_code == 404
    assert response.json() == {"detail": MOVIE_NOT_FOUND}


async def test_id_out_of_db_range_returns_422(client: AsyncClient) -> None:
    response = await client.get(f"/movies/{MAX_DB_ID + 1}")

    assert response.status_code == 422
