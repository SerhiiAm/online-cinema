import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_token, hash_password
from app.models.accounts import ActivationToken, User, UserGroup, UserGroupEnum


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
