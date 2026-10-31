import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from httpx import AsyncClient, Response
from kombu.exceptions import OperationalError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    TokenType,
    create_refresh_token,
    decode_token,
)
from app.models.accounts import (
    ActivationToken,
    RefreshToken,
    User,
    UserGroup,
    UserGroupEnum,
)
from app.routers.accounts import (
    ACCOUNT_NOT_ACTIVATED,
    ALREADY_ACTIVE,
    EMAIL_TAKEN,
    INVALID_ACTIVATION,
    INVALID_CREDENTIALS,
    INVALID_REFRESH_TOKEN,
    TOKEN_EXPIRED,
)

EMAIL = "user@example.com"
PASSWORD = "Str0ng!pass"


async def register(
    client: AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> Response:
    return await client.post(
        "/accounts/register", json={"email": email, "password": password}
    )


async def activate(client: AsyncClient, token: str, email: str = EMAIL) -> Response:
    return await client.post(
        "/accounts/activate", json={"email": email, "token": token}
    )


async def resend(client: AsyncClient, email: str = EMAIL) -> Response:
    return await client.post("/accounts/resend-activation", json={"email": email})


async def login(
    client: AsyncClient, email: str = EMAIL, password: str = PASSWORD
) -> Response:
    return await client.post(
        "/accounts/login", json={"email": email, "password": password}
    )


async def get_user(db: AsyncSession, email: str = EMAIL) -> User | None:
    result = await db.execute(
        select(User)
        .where(User.email == email)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_token(db: AsyncSession, email: str = EMAIL) -> ActivationToken | None:
    result = await db.execute(
        select(ActivationToken)
        .join(ActivationToken.user)
        .where(User.email == email)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def count_users(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User))


async def get_refresh_tokens(db: AsyncSession) -> list[RefreshToken]:
    return list(
        await db.scalars(
            select(RefreshToken)
            .order_by(RefreshToken.id)
            .execution_options(populate_existing=True)
        )
    )


async def refresh(client: AsyncClient, refresh_token: str) -> Response:
    return await client.post(
        "/accounts/refresh", json={"refresh_token": refresh_token}
    )


async def expire_token(db: AsyncSession, email: str = EMAIL) -> None:
    user = await get_user(db, email)
    await db.execute(
        update(ActivationToken)
        .where(ActivationToken.user_id == user.id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    await db.commit()


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


@pytest.mark.parametrize(
    "payload",
    [
        {"email": EMAIL, "password": "weak"},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": EMAIL},
        {},
    ],
    ids=["weak-password", "invalid-email", "missing-password", "empty-body"],
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


# --- POST /accounts/login ---


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


@pytest.mark.parametrize(
    "payload",
    [{"email": "not-an-email", "password": PASSWORD}, {"email": EMAIL}, {}],
    ids=["invalid-email", "missing-password", "empty-body"],
)
async def test_login_invalid_payload_returns_422(
    client: AsyncClient, payload: dict
) -> None:
    response = await client.post("/accounts/login", json=payload)

    assert response.status_code == 422


# --- POST /accounts/refresh ---


def make_refresh_jwt(
    expires_in: timedelta = timedelta(days=1), key: str = settings.jwt_secret_key
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1",
        "type": TokenType.REFRESH.value,
        "jti": "0" * 32,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, key, algorithm=settings.jwt_algorithm)


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


async def test_refresh_with_access_token_returns_401(
    client: AsyncClient, db_session: AsyncSession, token_pair: dict
) -> None:
    response = await refresh(client, token_pair["access_token"])

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}
    [stored] = await get_refresh_tokens(db_session)
    assert stored.revoked is False


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        make_refresh_jwt(key="a-different-secret-key-of-32-bytes!"),
        make_refresh_jwt(expires_in=timedelta(minutes=-1)),
    ],
    ids=["garbage", "wrong-signature", "expired"],
)
async def test_refresh_invalid_token_returns_401(
    client: AsyncClient, token: str
) -> None:
    response = await refresh(client, token)

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_REFRESH_TOKEN}


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


async def test_refresh_without_token_returns_422(client: AsyncClient) -> None:
    response = await client.post("/accounts/refresh", json={})

    assert response.status_code == 422
