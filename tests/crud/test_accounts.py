from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.accounts import (
    create_user,
    delete_expired_activation_tokens,
    get_user_by_id,
    promote_to_admin,
)
from app.models.accounts import ActivationToken, User, UserGroupEnum

PASSWORD = "Str0ng!pass"


async def create_user_with_token(
    db: AsyncSession, email: str, expired: bool
) -> ActivationToken:
    _, token = await create_user(db, email, PASSWORD)
    if expired:
        token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    return token


async def remaining_token_ids(db: AsyncSession) -> set[int]:
    return set(await db.scalars(select(ActivationToken.id)))


async def test_delete_expired_tokens_keeps_valid_ones(db_session: AsyncSession) -> None:
    await create_user_with_token(db_session, "old@example.com", expired=True)
    valid = await create_user_with_token(db_session, "new@example.com", expired=False)

    deleted = await delete_expired_activation_tokens(db_session)

    assert deleted == 1
    assert await remaining_token_ids(db_session) == {valid.id}


async def test_delete_expired_tokens_keeps_users(db_session: AsyncSession) -> None:
    await create_user_with_token(db_session, "old@example.com", expired=True)

    await delete_expired_activation_tokens(db_session)

    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


async def test_delete_expired_tokens_returns_zero_when_nothing_expired(
    db_session: AsyncSession,
) -> None:
    await create_user_with_token(db_session, "new@example.com", expired=False)

    assert await delete_expired_activation_tokens(db_session) == 0
    assert len(await remaining_token_ids(db_session)) == 1


async def test_promote_to_admin_makes_inactive_user_active_admin(
    db_session: AsyncSession,
) -> None:
    token = await create_user_with_token(
        db_session, "boss@example.com", expired=False
    )

    user_id = token.user_id
    await promote_to_admin(db_session, token.user)

    db_session.expire_all()  # read the result from the DB, not from memory
    user = await get_user_by_id(db_session, user_id)
    assert user is not None
    assert user.group.name == UserGroupEnum.ADMIN
    assert user.is_active
    assert await remaining_token_ids(db_session) == set()
