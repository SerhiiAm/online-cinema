"""GET /me and the get_current_user guard behind it: who may pass and who not."""

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import INACTIVE_ACCOUNT, INVALID_ACCESS_TOKEN
from app.core.security import create_access_token
from app.models.accounts import User
from tests.routers.accounts.helpers import EMAIL, get_me


async def test_me_returns_current_user(
    client: AsyncClient, active_user: User, token_pair: dict
) -> None:
    response = await get_me(client, token_pair["access_token"])

    assert response.status_code == 200
    # Exact match: also proves hashed_password is never sent to the client.
    assert response.json() == {
        "id": active_user.id,
        "email": EMAIL,
        "is_active": True,
        "group": "user",
    }


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/accounts/me")

    assert response.status_code == 401


async def test_me_with_refresh_token_returns_401(
    client: AsyncClient, token_pair: dict
) -> None:
    response = await get_me(client, token_pair["refresh_token"])

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_ACCESS_TOKEN}
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_me_with_expired_token_returns_401(
    client: AsyncClient, active_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "access_token_expire_minutes", -1)
    expired = create_access_token(active_user.id)

    response = await get_me(client, expired)

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_ACCESS_TOKEN}


async def test_me_deleted_user_returns_401(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    await db_session.execute(delete(User))
    await db_session.commit()

    response = await get_me(client, token_pair["access_token"])

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_ACCESS_TOKEN}


async def test_me_inactive_user_returns_403(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    await db_session.execute(update(User).values(is_active=False))
    await db_session.commit()

    response = await get_me(client, token_pair["access_token"])

    assert response.status_code == 403
    assert response.json() == {"detail": INACTIVE_ACCOUNT}
