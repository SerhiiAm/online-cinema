"""Forgotten password flow: POST /password-reset/request (email with a token)."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from kombu.exceptions import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_token
from app.models.accounts import User
from tests.routers.accounts.helpers import (
    EMAIL,
    get_reset_tokens,
    register,
    request_reset,
)

UNKNOWN_EMAIL = "nobody@example.com"
INACTIVE_EMAIL = "inactive@example.com"
RESET_REQUESTED = (
    "If the account exists and is active, a password reset link has been sent."
)


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
