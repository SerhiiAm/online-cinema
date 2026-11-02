from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.accounts import (
    create_user,
    delete_expired_activation_tokens,
    delete_expired_refresh_tokens,
    get_user_by_id,
    promote_to_admin,
)
from app.models.accounts import ActivationToken, RefreshToken, User, UserGroupEnum

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


async def test_delete_expired_refresh_tokens_keeps_unexpired_ones(
    db_session: AsyncSession,
) -> None:
    user, _ = await create_user(db_session, "user@example.com", PASSWORD)
    now = datetime.now(timezone.utc)
    past, future = now - timedelta(minutes=1), now + timedelta(days=1)
    tokens = {
        "expired": RefreshToken(jti="1" * 32, expires_at=past),
        "expired_revoked": RefreshToken(jti="2" * 32, expires_at=past, revoked=True),
        "valid": RefreshToken(jti="3" * 32, expires_at=future),
        # Needed to detect reuse on /refresh, so it must survive the cleanup.
        "revoked_not_expired": RefreshToken(
            jti="4" * 32, expires_at=future, revoked=True
        ),
    }
    for token in tokens.values():
        token.user_id = user.id
    db_session.add_all(tokens.values())
    await db_session.commit()

    deleted = await delete_expired_refresh_tokens(db_session)

    assert deleted == 2
    remaining = set(await db_session.scalars(select(RefreshToken.jti)))
    assert remaining == {tokens["valid"].jti, tokens["revoked_not_expired"].jti}
