import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import UserGroupEnum
from tests.routers.accounts.helpers import (
    activate,
    get_token,
    login,
    register,
    set_group,
)
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
async def admin_token(db_session: AsyncSession, user_token: str) -> str:
    # The group is read from the DB on every request, so the same token
    # works as an admin's right after the change.
    await set_group(db_session, UserGroupEnum.ADMIN)
    return user_token


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
