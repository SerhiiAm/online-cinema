"""POST /login: credentials check, token pair issuing, inactive accounts."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import DUMMY_PASSWORD_HASH, TokenType, decode_token
from app.models.accounts import User
from app.routers.accounts import ACCOUNT_NOT_ACTIVATED, INVALID_CREDENTIALS
from tests.routers.accounts.helpers import (
    EMAIL,
    PASSWORD,
    get_refresh_tokens,
    login,
)


async def test_login_returns_token_pair(
    client: AsyncClient, db_session: AsyncSession, active_user: User
) -> None:
    response = await login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    access = decode_token(body["access_token"], TokenType.ACCESS)
    refresh = decode_token(body["refresh_token"], TokenType.REFRESH)
    assert access["sub"] == refresh["sub"] == str(active_user.id)

    [stored] = await get_refresh_tokens(db_session)
    assert stored.jti == refresh["jti"]
    assert stored.user_id == active_user.id
    assert stored.revoked is False


async def test_login_accepts_email_in_any_case(
    client: AsyncClient, active_user: User
) -> None:
    response = await login(client, email="USER@Example.com")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "email, password",
    [(EMAIL, "Wr0ng!pass"), ("nobody@example.com", PASSWORD)],
    ids=["wrong-password", "unknown-email"],
)
async def test_login_invalid_credentials_returns_401(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    email: str,
    password: str,
) -> None:
    response = await login(client, email=email, password=password)

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CREDENTIALS}
    assert await get_refresh_tokens(db_session) == []


async def test_login_unknown_email_still_verifies_password(
    client: AsyncClient,
) -> None:
    # Guards the timing-attack protection: an unknown email must cost
    # a real password check, otherwise response time reveals registered emails.
    with patch(
        "app.routers.accounts.verify_password", return_value=False
    ) as verify_mock:
        await login(client, email="nobody@example.com")

    verify_mock.assert_called_once_with(PASSWORD, DUMMY_PASSWORD_HASH)


async def test_login_inactive_user_returns_403(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    response = await login(client)

    assert response.status_code == 403
    assert response.json() == {"detail": ACCOUNT_NOT_ACTIVATED}
    assert await get_refresh_tokens(db_session) == []


async def test_login_inactive_user_with_wrong_password_returns_401(
    client: AsyncClient, activation_token: str
) -> None:
    # The password is checked first, so 403 never reveals that
    # an inactive account exists to someone who doesn't know the password.
    response = await login(client, password="Wr0ng!pass")

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CREDENTIALS}


async def test_login_twice_creates_two_sessions(
    client: AsyncClient, db_session: AsyncSession, active_user: User
) -> None:
    first = await login(client)
    second = await login(client)

    assert first.status_code == second.status_code == 200
    tokens = await get_refresh_tokens(db_session)
    assert len(tokens) == 2
    assert tokens[0].jti != tokens[1].jti
    assert all(not token.revoked for token in tokens)
