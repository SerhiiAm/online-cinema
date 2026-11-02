"""Shared helpers for the /accounts tests: HTTP calls, DB lookups, fake tokens."""

from datetime import datetime, timedelta, timezone

import jwt
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import TokenType
from app.models.accounts import (
    ActivationToken,
    RefreshToken,
    User,
    UserGroup,
    UserGroupEnum,
)

EMAIL = "user@example.com"
PASSWORD = "Str0ng!pass"


# --- HTTP calls ---


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


async def refresh(client: AsyncClient, refresh_token: str) -> Response:
    return await client.post(
        "/accounts/refresh", json={"refresh_token": refresh_token}
    )


async def logout(client: AsyncClient, refresh_token: str) -> Response:
    return await client.post(
        "/accounts/logout", json={"refresh_token": refresh_token}
    )


async def get_me(client: AsyncClient, access_token: str) -> Response:
    return await client.get(
        "/accounts/me", headers={"Authorization": f"Bearer {access_token}"}
    )


async def change_group(
    client: AsyncClient, access_token: str, user_id: int, group: str
) -> Response:
    return await client.patch(
        f"/accounts/users/{user_id}/group",
        json={"group": group},
        headers={"Authorization": f"Bearer {access_token}"},
    )


# --- DB lookups (populate_existing: always read fresh data, not the cache) ---


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


async def get_group(db: AsyncSession, email: str = EMAIL) -> UserGroupEnum:
    return await db.scalar(
        select(UserGroup.name).join(User.group).where(User.email == email)
    )


async def set_group(
    db: AsyncSession, group: UserGroupEnum, email: str = EMAIL
) -> None:
    group_id = select(UserGroup.id).where(UserGroup.name == group).scalar_subquery()
    await db.execute(
        update(User).where(User.email == email).values(group_id=group_id)
    )
    await db.commit()


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


async def expire_token(db: AsyncSession, email: str = EMAIL) -> None:
    user = await get_user(db, email)
    await db.execute(
        update(ActivationToken)
        .where(ActivationToken.user_id == user.id)
        .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    await db.commit()


# --- Fake tokens ---


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
