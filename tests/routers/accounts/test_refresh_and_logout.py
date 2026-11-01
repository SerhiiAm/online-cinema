"""Refresh token lifecycle: POST /refresh (rotation, reuse detection)
and POST /logout (revoking one session).

Both endpoints accept the same body and reject bad tokens the same way,
so those checks are shared and run against each endpoint.
"""

import asyncio
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenType, create_refresh_token, decode_token
from app.models.accounts import User
from app.routers.accounts import INVALID_REFRESH_TOKEN
from tests.routers.accounts.helpers import (
    get_refresh_tokens,
    login,
    logout,
    make_refresh_jwt,
    refresh,
)

ENDPOINTS = pytest.mark.parametrize(
    "path", ["/accounts/refresh", "/accounts/logout"], ids=["refresh", "logout"]
)


# --- Shared: bad input is rejected by both endpoints ---


@ENDPOINTS
async def test_access_token_instead_of_refresh_returns_401(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict, path: str
) -> None:
    response = await client.post(
        path, json={"refresh_token": token_pair["access_token"]}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}
    [stored] = await get_refresh_tokens(db_session)
    assert stored.revoked is False


@ENDPOINTS
@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        make_refresh_jwt(key="a-different-secret-key-of-32-bytes!"),
        make_refresh_jwt(expires_in=timedelta(minutes=-1)),
    ],
    ids=["garbage", "wrong-signature", "expired"],
)
async def test_invalid_token_returns_401(
    client: AsyncClient, path: str, token: str
) -> None:
    response = await client.post(path, json={"refresh_token": token})

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}


# --- POST /accounts/refresh ---


async def test_refresh_returns_new_token_pair(
    client: AsyncClient, db_session: AsyncSession, active_user: User, token_pair: dict
) -> None:
    response = await refresh(client, token_pair["refresh_token"])

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["refresh_token"] != token_pair["refresh_token"]

    access = decode_token(body["access_token"], TokenType.ACCESS)
    new_refresh = decode_token(body["refresh_token"], TokenType.REFRESH)
    assert access["sub"] == new_refresh["sub"] == str(active_user.id)

    old_jti = decode_token(token_pair["refresh_token"], TokenType.REFRESH)["jti"]
    old, new = await get_refresh_tokens(db_session)
    assert (old.jti, old.revoked) == (old_jti, True)
    assert (new.jti, new.revoked) == (new_refresh["jti"], False)


async def test_refresh_new_token_can_be_refreshed_again(
    client: AsyncClient, token_pair: dict
) -> None:
    first = await refresh(client, token_pair["refresh_token"])

    second = await refresh(client, first.json()["refresh_token"])

    assert second.status_code == 200


async def test_refresh_reused_token_revokes_all_sessions(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    other_device = (await login(client)).json()
    rotated = (await refresh(client, token_pair["refresh_token"])).json()

    response = await refresh(client, token_pair["refresh_token"])

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}
    assert all(token.revoked for token in await get_refresh_tokens(db_session))
    assert (await refresh(client, rotated["refresh_token"])).status_code == 401
    assert (await refresh(client, other_device["refresh_token"])).status_code == 401


async def test_refresh_unknown_token_does_not_revoke_sessions(
    client: AsyncClient, db_session: AsyncSession, active_user: User, token_pair: dict
) -> None:
    # Correctly signed, but never saved to the DB.
    unknown = create_refresh_token(active_user.id).token

    response = await refresh(client, unknown)

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}
    [stored] = await get_refresh_tokens(db_session)
    assert stored.revoked is False


async def test_concurrent_refresh_with_same_token(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    # Only one request may rotate the token; the other one is treated
    # as reuse, so the whole session chain is revoked (strict variant).
    responses = await asyncio.gather(
        refresh(client, token_pair["refresh_token"]),
        refresh(client, token_pair["refresh_token"]),
    )

    assert sorted(r.status_code for r in responses) == [200, 401]
    assert all(token.revoked for token in await get_refresh_tokens(db_session))


# --- POST /accounts/logout ---


async def test_logout_revokes_refresh_token(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    response = await logout(client, token_pair["refresh_token"])

    assert response.status_code == 200
    assert response.json() == {"message": "Logged out successfully."}
    [stored] = await get_refresh_tokens(db_session)
    assert stored.revoked is True
    assert (await refresh(client, token_pair["refresh_token"])).status_code == 401


async def test_logout_keeps_other_sessions(
    client: AsyncClient, token_pair: dict
) -> None:
    other_device = (await login(client)).json()

    await logout(client, token_pair["refresh_token"])

    assert (await refresh(client, other_device["refresh_token"])).status_code == 200


async def test_logout_twice_does_not_revoke_other_sessions(
    client: AsyncClient, token_pair: dict
) -> None:
    # A double click must not be treated as reuse of a stolen token.
    other_device = (await login(client)).json()
    await logout(client, token_pair["refresh_token"])

    response = await logout(client, token_pair["refresh_token"])

    assert response.status_code == 200
    assert (await refresh(client, other_device["refresh_token"])).status_code == 200
