"""Account creation flow: POST /register, /activate, /resend-activation."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from kombu.exceptions import OperationalError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.accounts import UserGroup, UserGroupEnum
from app.routers.accounts import (
    ALREADY_ACTIVE,
    EMAIL_TAKEN,
    INVALID_ACTIVATION,
    TOKEN_EXPIRED,
)
from tests.routers.accounts.helpers import (
    EMAIL,
    PASSWORD,
    activate,
    count_users,
    expire_token,
    get_token,
    get_user,
    register,
    resend,
)

# --- POST /accounts/register ---


async def test_register_creates_inactive_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await register(client)

    assert response.status_code == 201
    assert response.json() == {"id": 1, "email": EMAIL, "is_active": False}

    user = await get_user(db_session)
    assert user is not None
    assert user.is_active is False
    assert user.hashed_password != PASSWORD
    assert user.hashed_password.startswith("$argon2id$")

    group_name = await db_session.scalar(
        select(UserGroup.name).where(UserGroup.id == user.group_id)
    )
    assert group_name == UserGroupEnum.USER


async def test_register_issues_activation_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await register(client)

    token = await get_token(db_session)
    assert token is not None

    expected = datetime.now(timezone.utc) + timedelta(
        hours=settings.activation_token_expire_hours
    )
    assert abs(token.expires_at - expected) < timedelta(minutes=1)


async def test_register_lowercases_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await register(client, email="User@Example.COM")

    assert response.status_code == 201
    assert response.json()["email"] == EMAIL
    assert await get_user(db_session) is not None


@pytest.mark.parametrize(
    "duplicate_email",
    [EMAIL, "USER@example.com"],
    ids=["same-case", "different-case"],
)
async def test_register_duplicate_email_returns_409(
    client: AsyncClient, db_session: AsyncSession, duplicate_email: str
) -> None:
    await register(client)

    response = await register(client, email=duplicate_email)

    assert response.status_code == 409
    assert response.json() == {"detail": EMAIL_TAKEN}
    assert await count_users(db_session) == 1


# Only our own rules: that the schema really uses StrongPassword and EmailStr.
# Missing fields / empty body are FastAPI's job and are not tested here.
@pytest.mark.parametrize(
    "payload",
    [
        {"email": EMAIL, "password": "weak"},
        {"email": "not-an-email", "password": PASSWORD},
    ],
    ids=["weak-password", "invalid-email"],
)
async def test_register_invalid_payload_returns_422(
    client: AsyncClient, db_session: AsyncSession, payload: dict
) -> None:
    response = await client.post("/accounts/register", json=payload)

    assert response.status_code == 422
    assert await count_users(db_session) == 0


async def test_concurrent_registrations_create_single_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    responses = await asyncio.gather(*(register(client) for _ in range(5)))

    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409]
    assert await count_users(db_session) == 1


async def test_register_sends_activation_email(
    client: AsyncClient,
    db_session: AsyncSession,
    send_activation_email_mock: MagicMock,
) -> None:
    await register(client)

    token = await get_token(db_session)
    send_activation_email_mock.assert_called_once_with(EMAIL, token.token)


async def test_register_duplicate_email_does_not_send_email(
    client: AsyncClient, send_activation_email_mock: MagicMock
) -> None:
    await register(client)
    send_activation_email_mock.reset_mock()

    await register(client)

    send_activation_email_mock.assert_not_called()


async def test_register_succeeds_when_broker_is_unavailable(
    client: AsyncClient,
    db_session: AsyncSession,
    send_activation_email_mock: MagicMock,
) -> None:
    send_activation_email_mock.side_effect = OperationalError("Redis is down")

    response = await register(client)

    assert response.status_code == 201
    assert await get_user(db_session) is not None


# --- POST /accounts/activate ---


async def test_activate_activates_user_and_deletes_token(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    response = await activate(client, activation_token)

    assert response.status_code == 200
    assert response.json() == {"message": "Account activated successfully."}

    user = await get_user(db_session)
    assert user.is_active is True
    assert await get_token(db_session) is None


async def test_activate_accepts_email_in_any_case(
    client: AsyncClient, activation_token: str
) -> None:
    response = await activate(client, activation_token, email="USER@Example.com")

    assert response.status_code == 200


async def test_activate_with_wrong_token_returns_400(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    response = await activate(client, "wrong-token")

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_ACTIVATION}
    assert (await get_user(db_session)).is_active is False


async def test_activate_unknown_email_returns_400(
    client: AsyncClient, activation_token: str
) -> None:
    response = await activate(client, activation_token, email="other@example.com")

    assert response.status_code == 400
    assert response.json() == {"detail": INVALID_ACTIVATION}


async def test_activate_already_active_returns_400(
    client: AsyncClient, activation_token: str
) -> None:
    await activate(client, activation_token)

    response = await activate(client, activation_token)

    assert response.status_code == 400
    assert response.json() == {"detail": ALREADY_ACTIVE}


async def test_activate_expired_token_returns_400(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    await expire_token(db_session)

    response = await activate(client, activation_token)

    assert response.status_code == 400
    assert response.json() == {"detail": TOKEN_EXPIRED}
    assert (await get_user(db_session)).is_active is False


# --- POST /accounts/resend-activation ---


async def test_resend_replaces_token(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    response = await resend(client)

    assert response.status_code == 200
    new_token = await get_token(db_session)
    assert new_token.token != activation_token

    old_token_response = await activate(client, activation_token)
    assert old_token_response.status_code == 400
    assert old_token_response.json() == {"detail": INVALID_ACTIVATION}


async def test_resend_after_expiry_issues_working_token(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    await expire_token(db_session)

    await resend(client)
    new_token = await get_token(db_session)

    assert new_token.expires_at > datetime.now(timezone.utc)
    response = await activate(client, new_token.token)
    assert response.status_code == 200


async def test_resend_for_active_user_does_not_issue_token(
    client: AsyncClient, db_session: AsyncSession, activation_token: str
) -> None:
    await activate(client, activation_token)

    response = await resend(client)

    assert response.status_code == 200
    assert await get_token(db_session) is None


async def test_resend_response_does_not_reveal_registered_emails(
    client: AsyncClient, activation_token: str
) -> None:
    registered = await resend(client)
    unknown = await resend(client, email="nobody@example.com")

    assert registered.status_code == unknown.status_code == 200
    assert registered.json() == unknown.json()


async def test_resend_sends_email_with_new_token(
    client: AsyncClient,
    db_session: AsyncSession,
    activation_token: str,
    send_activation_email_mock: MagicMock,
) -> None:
    send_activation_email_mock.reset_mock()

    await resend(client)

    new_token = await get_token(db_session)
    send_activation_email_mock.assert_called_once_with(EMAIL, new_token.token)


async def test_concurrent_resends_keep_one_working_token(
    client: AsyncClient,
    db_session: AsyncSession,
    activation_token: str,
    send_activation_email_mock: MagicMock,
) -> None:
    send_activation_email_mock.reset_mock()

    responses = await asyncio.gather(resend(client), resend(client))

    assert [r.status_code for r in responses] == [200, 200]
    sent = {call.args[1] for call in send_activation_email_mock.call_args_list}
    stored = await get_token(db_session)
    # The token from one of the two emails works.
    assert stored.token in sent
    assert (await activate(client, stored.token)).status_code == 200


@pytest.mark.parametrize(
    "activate_first, email",
    [(True, EMAIL), (False, "nobody@example.com")],
    ids=["active-user", "unknown-email"],
)
async def test_resend_does_not_send_email(
    client: AsyncClient,
    activation_token: str,
    send_activation_email_mock: MagicMock,
    activate_first: bool,
    email: str,
) -> None:
    if activate_first:
        await activate(client, activation_token)
    send_activation_email_mock.reset_mock()

    await resend(client, email=email)

    send_activation_email_mock.assert_not_called()
