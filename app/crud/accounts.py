import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_refresh_token, generate_token, hash_password
from app.models.accounts import (
    ActivationToken,
    RefreshToken,
    User,
    UserGroup,
    UserGroupEnum,
)


def _activation_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        hours=settings.activation_token_expire_hours
    )


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def get_user_group(db: AsyncSession, name: UserGroupEnum) -> UserGroup:
    result = await db.execute(select(UserGroup).where(UserGroup.name == name))
    return result.scalar_one()


async def get_activation_token(
    db: AsyncSession, user_id: int
) -> ActivationToken | None:
    result = await db.execute(
        select(ActivationToken).where(ActivationToken.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession, email: str, password: str
) -> tuple[User, ActivationToken]:
    group = await get_user_group(db, UserGroupEnum.USER)

    user = User(
        email=email.lower(),
        hashed_password=await asyncio.to_thread(hash_password, password),
        group_id=group.id,
    )
    activation_token = ActivationToken(
        user=user,
        token=generate_token(),
        expires_at=_activation_expires_at(),
    )

    db.add_all([user, activation_token])
    await db.commit()
    return user, activation_token


async def activate_user(db: AsyncSession, user: User) -> None:
    user.is_active = True
    await db.execute(
        delete(ActivationToken).where(ActivationToken.user_id == user.id)
    )
    await db.commit()


async def refresh_activation_token(
    db: AsyncSession, user: User
) -> ActivationToken:
    await db.execute(
        delete(ActivationToken).where(ActivationToken.user_id == user.id)
    )
    activation_token = ActivationToken(
        user_id=user.id,
        token=generate_token(),
        expires_at=_activation_expires_at(),
    )

    db.add(activation_token)
    await db.commit()
    return activation_token


async def issue_refresh_token(db: AsyncSession, user_id: int) -> str:
    refresh = create_refresh_token(user_id)
    db.add(
        RefreshToken(user_id=user_id, jti=refresh.jti, expires_at=refresh.expires_at)
    )
    await db.commit()
    return refresh.token


async def get_refresh_token(db: AsyncSession, jti: str) -> RefreshToken | None:
    result = await db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    return result.scalar_one_or_none()


async def rotate_refresh_token(db: AsyncSession, jti: str) -> tuple[int, str] | None:
    # Revoke-and-check in one UPDATE: of two concurrent requests with the same
    # token, Postgres lets only one match the row, the other gets None.
    user_id = await db.scalar(
        update(RefreshToken)
        .where(
            RefreshToken.jti == jti,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > func.now(),
        )
        .values(revoked=True)
        .returning(RefreshToken.user_id)
    )
    if user_id is None:
        return None
    # issue_refresh_token commits, so the old token is revoked
    # and the new one saved in the same transaction.
    return user_id, await issue_refresh_token(db, user_id)


async def revoke_refresh_token(db: AsyncSession, jti: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.jti == jti, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    await db.commit()


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    await db.commit()


async def delete_expired_activation_tokens(db: AsyncSession) -> int:
    result = await db.execute(
        delete(ActivationToken).where(
            ActivationToken.expires_at < datetime.now(timezone.utc)
        )
    )
    await db.commit()
    return result.rowcount
