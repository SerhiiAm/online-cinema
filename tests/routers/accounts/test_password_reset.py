"""Forgotten password flow: POST /password-reset/request (email with a token)
and POST /password-reset/confirm (new password with that token)."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from kombu.exceptions import OperationalError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_token
from app.models.accounts import PasswordResetToken, User
from app.routers.accounts import INVALID_RESET_TOKEN
from tests.routers.accounts.helpers import (
    EMAIL,
    PASSWORD,
    add_reset_token,
    confirm_reset,
    get_refresh_tokens,
    get_reset_tokens,
    login,
    refresh,
    register,
    request_reset,
)

UNKNOWN_EMAIL = "nobody@example.com"
INACTIVE_EMAIL = "inactive@example.com"
OTHER_EMAIL = "other@example.com"
NEW_PASSWORD = "N3w!passw0rd"
RESET_REQUESTED = (
    "If the account exists and is active, a password reset link has been sent."
)
RESET_DONE = "Password has been reset successfully. Log in with the new password."


def sent_token(mock: MagicMock) -> str:
    mock.assert_called_once()
    email, token = mock.call_args.args
    assert email == EMAIL
    return token


# --- POST /accounts/password-reset/request ---


async def test_request_reset_saves_hash_and_emails_token(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    send_password_reset_email_mock: MagicMock,
) -> None:
    response = await request_reset(client)

    assert response.status_code == 200
    assert response.json() == {"message": RESET_REQUESTED}
    token = sent_token(send_password_reset_email_mock)
    [stored] = await get_reset_tokens(db_session)
    assert stored.user_id == active_user.id
    # Only the hash is in the DB, the token itself only in the email.
    assert stored.token == hash_token(token)
    expected_expiry = datetime.now(timezone.utc) + timedelta(
        minutes=settings.password_reset_token_expire_minutes
    )
    assert abs(stored.expires_at - expected_expiry) < timedelta(seconds=10)


async def test_request_reset_again_replaces_token(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    send_password_reset_email_mock: MagicMock,
) -> None:
    await request_reset(client)
    first = sent_token(send_password_reset_email_mock)
    send_password_reset_email_mock.reset_mock()

    await request_reset(client)

    second = sent_token(send_password_reset_email_mock)
    [stored] = await get_reset_tokens(db_session)
    assert stored.token == hash_token(second) != hash_token(first)


@pytest.mark.parametrize(
    "email", [UNKNOWN_EMAIL, INACTIVE_EMAIL], ids=["unknown", "inactive"]
)
async def test_request_reset_ignores_unknown_and_inactive_accounts(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    send_password_reset_email_mock: MagicMock,
    email: str,
) -> None:
    await register(client, email=INACTIVE_EMAIL)
    active_response = await request_reset(client)
    send_password_reset_email_mock.reset_mock()

    response = await request_reset(client, email=email)

    # Same answer as for an active account: no way to probe emails.
    assert response.status_code == 200
    assert response.json() == active_response.json()
    send_password_reset_email_mock.assert_not_called()
    [stored] = await get_reset_tokens(db_session)
    assert stored.user_id == active_user.id


async def test_request_reset_succeeds_when_broker_is_unavailable(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    send_password_reset_email_mock: MagicMock,
) -> None:
    send_password_reset_email_mock.side_effect = OperationalError("Redis is down")

    response = await request_reset(client)

    assert response.status_code == 200
    assert len(await get_reset_tokens(db_session)) == 1


async def test_concurrent_reset_requests_keep_one_token(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    send_password_reset_email_mock: MagicMock,
) -> None:
    responses = await asyncio.gather(request_reset(client), request_reset(client))

    assert [r.status_code for r in responses] == [200, 200]
    sent = {call.args[1] for call in send_password_reset_email_mock.call_args_list}
    [stored] = await get_reset_tokens(db_session)
    # Only the token from one of the two emails works.
    assert stored.token in {hash_token(token) for token in sent}


# --- POST /accounts/password-reset/confirm ---


@pytest.fixture
async def reset_token(db_session: AsyncSession, active_user: User) -> str:
    return await add_reset_token(db_session, active_user.id)


async def expire_reset_token(db: AsyncSession) -> None:
    await db.execute(
        update(PasswordResetToken).values(
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    )
    await db.commit()


async def test_full_reset_flow_sets_new_password(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    send_password_reset_email_mock: MagicMock,
) -> None:
    await request_reset(client)
    token = sent_token(send_password_reset_email_mock)

    response = await confirm_reset(client, token, NEW_PASSWORD)

    assert response.status_code == 200
    assert response.json() == {"message": RESET_DONE}
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200
    assert (await login(client, password=PASSWORD)).status_code == 401
    assert await get_reset_tokens(db_session) == []


async def test_confirm_reset_logs_out_all_sessions(
    client: AsyncClient,
    db_session: AsyncSession,
    token_pair: dict,
    reset_token: str,
) -> None:
    other_device = (await login(client)).json()

    await confirm_reset(client, reset_token, NEW_PASSWORD)

    assert all(token.revoked for token in await get_refresh_tokens(db_session))
    assert (await refresh(client, token_pair["refresh_token"])).status_code == 401
    assert (await refresh(client, other_device["refresh_token"])).status_code == 401


async def test_confirm_reset_token_is_single_use(
    client: AsyncClient, reset_token: str
) -> None:
    await confirm_reset(client, reset_token, NEW_PASSWORD)

    response = await confirm_reset(client, reset_token, "An0ther!pass")

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_RESET_TOKEN}
    assert (await login(client, password=NEW_PASSWORD)).status_code == 200


async def test_confirm_reset_allows_the_same_password(
    client: AsyncClient, reset_token: str
) -> None:
    # Agreed: the goal of a reset is to regain access, not a new password.
    response = await confirm_reset(client, reset_token, PASSWORD)

    assert response.status_code == 200


@pytest.mark.parametrize(
    "case", ["wrong-token", "other-users-email", "unknown-email", "expired"]
)
async def test_confirm_reset_rejects_bad_token_and_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    token_pair: dict,
    reset_token: str,
    case: str,
) -> None:
    token, email = reset_token, EMAIL
    if case == "wrong-token":
        token = reset_token[:-1] + ("A" if reset_token[-1] != "A" else "B")
    elif case == "other-users-email":
        await register(client, email=OTHER_EMAIL)
        email = OTHER_EMAIL
    elif case == "unknown-email":
        email = UNKNOWN_EMAIL
    else:
        await expire_reset_token(db_session)

    response = await confirm_reset(client, token, NEW_PASSWORD, email=email)

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_RESET_TOKEN}
    assert (await login(client)).status_code == 200
    assert all(not token.revoked for token in await get_refresh_tokens(db_session))
    # A wrong guess does not use up the real token.
    assert len(await get_reset_tokens(db_session)) == 1


async def test_confirm_reset_weak_password_keeps_token(
    client: AsyncClient, reset_token: str
) -> None:
    response = await confirm_reset(client, reset_token, "weakpass")

    assert response.status_code == 422
    # The user can fix the password and use the same link again.
    assert (await confirm_reset(client, reset_token, NEW_PASSWORD)).status_code == 200


async def test_concurrent_confirms_with_same_token(
    client: AsyncClient, reset_token: str
) -> None:
    passwords = ["Aaaa!1111", "Bbbb!2222"]

    responses = await asyncio.gather(
        *(confirm_reset(client, reset_token, password) for password in passwords)
    )

    assert sorted(r.status_code for r in responses) == [200, 400]
    [winner] = [p for p, r in zip(passwords, responses) if r.is_success]
    assert (await login(client, password=winner)).status_code == 200
