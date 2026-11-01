"""Fixtures for the /accounts tests, each one builds on the previous:
activation_token (registered) -> active_user (activated) -> token_pair (logged in).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import User
from tests.routers.accounts.helpers import (
    activate,
    get_token,
    get_user,
    login,
    register,
)


@pytest.fixture
async def activation_token(client: AsyncClient, db_session: AsyncSession) -> str:
    await register(client)
    token = await get_token(db_session)
    return token.token


@pytest.fixture
async def active_user(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> User:
    await activate(client, activation_token)
    return await get_user(db_session)


@pytest.fixture
async def token_pair(client: AsyncClient, active_user: User) -> dict:
    response = await login(client)
    return response.json()
