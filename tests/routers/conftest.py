import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.movies import Certification, Director, Genre, Star
from tests.routers.accounts.helpers import activate, get_token, login, register
from tests.routers.movies.helpers import add_item


@pytest.fixture
async def user_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Access token of an active user from the plain `user` group."""
    await register(client)
    activation_token = await get_token(db_session)
    await activate(client, activation_token.token)
    response = await login(client)
    return response.json()["access_token"]


@pytest.fixture
async def refs(db_session: AsyncSession) -> dict[str, int]:
    """Ids of a certification, two genres, a star and a director."""
    items = {
        "certification": await add_item(db_session, Certification, "PG-13"),
        "drama": await add_item(db_session, Genre, "Drama"),
        "action": await add_item(db_session, Genre, "Action"),
        "star": await add_item(db_session, Star, "Leonardo DiCaprio"),
        "director": await add_item(db_session, Director, "Christopher Nolan"),
    }
    return {key: item.id for key, item in items.items()}
