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
from tests.routers.movies.helpers import REFERENCE_CASES, ReferenceCase


@pytest.fixture
async def user_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Access token of an active user from the plain `user` group."""
    await register(client)
    activation_token = await get_token(db_session)
    await activate(client, activation_token.token)
    response = await login(client)
    return response.json()["access_token"]


@pytest.fixture
async def moderator_token(db_session: AsyncSession, user_token: str) -> str:
    # The group is read from the DB on every request, so the same token
    # works as a moderator's right after the change.
    await set_group(db_session, UserGroupEnum.MODERATOR)
    return user_token


@pytest.fixture(params=REFERENCE_CASES, ids=lambda case: case.path.strip("/"))
def ref(request: pytest.FixtureRequest) -> ReferenceCase:
    """Runs a test once for each of genres, stars, directors, certifications."""
    return request.param
