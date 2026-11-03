import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import ScalarSelect, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.core.security import (
    create_refresh_token,
    generate_token,
    hash_password,
    hash_token,
)
from app.models.accounts import (
    ActivationToken,
    PasswordResetToken,
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


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    # The group is loaded in the same query: lazy loading user.group
    # later would fail in async code (MissingGreenlet).
    result = await db.execute(
        select(User).options(joinedload(User.group)).where(User.id == user_id)
    )
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


async def promote_to_admin(db: AsyncSession, user: User) -> None:
    # An admin who can't log in is useless, so the account is activated too
    # (activate_user also deletes a leftover activation token and commits).
    admin_group = await get_user_group(db, UserGroupEnum.ADMIN)
    user.group_id = admin_group.id
    await activate_user(db, user)


def _group_id(name: UserGroupEnum) -> ScalarSelect[int]:
    return select(UserGroup.id).where(UserGroup.name == name).scalar_subquery()


async def change_user_group(
    db: AsyncSession, user_id: int, group: UserGroupEnum
) -> bool:
    # Admins can't be changed here. The check is part of the same UPDATE,
    # so a user promoted to admin in parallel can't slip in between
    # the check and the write. Returns False if nothing was updated.
    updated_id = await db.scalar(
        update(User)
        .where(User.id == user_id, User.group_id != _group_id(UserGroupEnum.ADMIN))
        .values(group_id=_group_id(group))
        .returning(User.id)
    )
    await db.commit()
    return updated_id is not None


async def refresh_activation_token(
    db: AsyncSession, user: User
) -> ActivationToken:
    # Upsert instead of delete + insert: two parallel resends would both
    # delete, then the second insert would hit UNIQUE(user_id) -> 500.
    values = {"token": generate_token(), "expires_at": _activation_expires_at()}
    activation_token = await db.scalar(
        insert(ActivationToken)
        .values(user_id=user.id, **values)
        .on_conflict_do_update(index_elements=["user_id"], set_=values)
        .returning(ActivationToken)
        # The row keeps its id, so a copy already in the session
        # must be overwritten with the new token, not returned as is.
        .execution_options(populate_existing=True)
    )
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


async def issue_password_reset_token(db: AsyncSession, user_id: int) -> str:
    # One token per user, so a new request makes the previous email useless.
    # Upsert instead of delete + insert: two parallel requests would both
    # delete, then the second insert would hit UNIQUE(user_id) -> 500.
    token = generate_token()
    values = {
        "token": hash_token(token),
        "expires_at": datetime.now(timezone.utc)
        + timedelta(minutes=settings.password_reset_token_expire_minutes),
    }
    await db.execute(
        insert(PasswordResetToken)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(index_elements=["user_id"], set_=values)
    )
    await db.commit()
    return token


async def use_password_reset_token(
    db: AsyncSession, email: str, token: str
) -> User | None:
    # Check and use up the token in one DELETE: of two parallel requests
    # with the same token Postgres lets only one delete the row.
    # Not committed here: set_password commits it together with the new
    # password, so a failed request leaves the token usable.
    user_id = await db.scalar(
        delete(PasswordResetToken)
        .where(
            PasswordResetToken.user_id
            == select(User.id).where(User.email == email.lower()).scalar_subquery(),
            PasswordResetToken.token == hash_token(token),
            PasswordResetToken.expires_at > func.now(),
        )
        .returning(PasswordResetToken.user_id)
    )
    if user_id is None:
        return None
    return await get_user_by_id(db, user_id)


async def set_password(db: AsyncSession, user: User, password: str) -> None:
    # One transaction: the new password, no pending reset link (an old email
    # must not undo the change) and every session logged out.
    user.hashed_password = await asyncio.to_thread(hash_password, password)
    await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )
    # Commits everything above together with the revoke.
    await revoke_all_refresh_tokens(db, user.id)


async def delete_expired_activation_tokens(db: AsyncSession) -> int:
    result = await db.execute(
        delete(ActivationToken).where(
            ActivationToken.expires_at < datetime.now(timezone.utc)
        )
    )
    await db.commit()
    return result.rowcount


async def delete_expired_refresh_tokens(db: AsyncSession) -> int:
    # Only expired rows: a revoked but not yet expired token must stay,
    # otherwise its reuse would no longer be detected on /refresh.
    result = await db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < func.now())
    )
    await db.commit()
    return result.rowcount
