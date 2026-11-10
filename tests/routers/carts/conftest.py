import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.routers.accounts.helpers import activate, get_token, login, register
from tests.routers.carts.helpers import OTHER_EMAIL
from tests.routers.movies.helpers import save_movie


@pytest.fixture
async def other_token(
    client: AsyncClient, db_session: AsyncSession, user_token: str
) -> str:
    """Access token of a second active user (needs `user_token` first)."""
    await register(client, OTHER_EMAIL)
    activation_token = await get_token(db_session, OTHER_EMAIL)
    await activate(client, activation_token.token, OTHER_EMAIL)
    response = await login(client, OTHER_EMAIL)
    return response.json()["access_token"]


@pytest.fixture
async def movies(db_session: AsyncSession, refs: dict[str, int]) -> dict[str, int]:
    """Ids of three saved movies by name."""
    return {
        "Inception": await save_movie(
            db_session,
            refs,
            "Inception",
            price="19.99",
            genre_ids=[refs["drama"], refs["action"]],
        ),
        "Memento": await save_movie(db_session, refs, "Memento", price="5.01"),
        "Tenet": await save_movie(db_session, refs, "Tenet", price="7.50"),
    }
