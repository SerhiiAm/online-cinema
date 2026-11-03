"""POST /accounts/change-password: the old password is required,
and a successful change logs out every session."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import User
from app.routers.accounts import SAME_PASSWORD, WRONG_PASSWORD
from tests.routers.accounts.helpers import (
    PASSWORD,
    add_reset_token,
    change_password,
    get_refresh_tokens,
    get_reset_tokens,
    login,
    refresh,
)

NEW_PASSWORD = "N3w!passw0rd"
WRONG_OLD_PASSWORD = "Wr0ng!pass"


async def assert_nothing_changed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    assert all(not token.revoked for token in await get_refresh_tokens(db_session))
    assert len(await get_reset_tokens(db_session)) == 1
    assert (await login(client)).status_code == 200


async def test_change_password_sets_new_password(
    client: AsyncClient, token_pair: dict
) -> None:
    response = await change_password(
        client, token_pair["access_token"], PASSWORD, NEW_PASSWORD
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Password changed successfully. "
        "Log in again with the new password."
    }
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200
    assert (await login(client, password=PASSWORD)).status_code == 401


async def test_change_password_logs_out_all_sessions(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    other_device = (await login(client)).json()

    await change_password(client, token_pair["access_token"], PASSWORD, NEW_PASSWORD)

    assert all(token.revoked for token in await get_refresh_tokens(db_session))
    assert (await refresh(client, token_pair["refresh_token"])).status_code == 401
    assert (await refresh(client, other_device["refresh_token"])).status_code == 401


async def test_change_password_invalidates_pending_reset_token(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    token_pair: dict,
) -> None:
    # Otherwise an old reset email could undo the change.
    await add_reset_token(db_session, active_user.id)

    await change_password(client, token_pair["access_token"], PASSWORD, NEW_PASSWORD)

    assert await get_reset_tokens(db_session) == []


@pytest.mark.parametrize(
    "new_password",
    [NEW_PASSWORD, WRONG_OLD_PASSWORD],
    # The second case: a wrong old password is reported first,
    # even if the new one equals it.
    ids=["different-new", "new-equals-wrong-old"],
)
async def test_wrong_old_password_returns_400_and_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    token_pair: dict,
    new_password: str,
) -> None:
    await add_reset_token(db_session, active_user.id)

    response = await change_password(
        client, token_pair["access_token"], WRONG_OLD_PASSWORD, new_password
    )

    assert response.status_code == 400
    assert response.json() == {"detail": WRONG_PASSWORD}
    await assert_nothing_changed(client, db_session)


async def test_same_password_returns_400_and_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    token_pair: dict,
) -> None:
    await add_reset_token(db_session, active_user.id)

    response = await change_password(
        client, token_pair["access_token"], PASSWORD, PASSWORD
    )

    assert response.status_code == 400
    assert response.json() == {"detail": SAME_PASSWORD}
    await assert_nothing_changed(client, db_session)


async def test_weak_new_password_returns_422(
    client: AsyncClient, token_pair: dict
) -> None:
    response = await change_password(
        client, token_pair["access_token"], PASSWORD, "weakpass"
    )

    assert response.status_code == 422
    assert (await login(client)).status_code == 200


async def test_change_password_requires_access_token(
    client: AsyncClient, active_user: User
) -> None:
    response = await client.post(
        "/accounts/change-password",
        json={"old_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401
    assert (await login(client)).status_code == 200
