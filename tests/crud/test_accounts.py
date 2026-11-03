from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.crud.accounts import (
    create_user,
    delete_expired_activation_tokens,
    delete_expired_password_reset_tokens,
    delete_expired_refresh_tokens,
    get_user_by_id,
    promote_to_admin,
    refresh_activation_token,
    set_password,
)
from app.models.accounts import (
    ActivationToken,
    PasswordResetToken,
    RefreshToken,
    User,
    UserGroupEnum,
)

PASSWORD = "Str0ng!pass"
NEW_PASSWORD = "N3w!passw0rd"


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
    valid_id = valid.id  # rollback() below expires the object

    deleted = await delete_expired_activation_tokens(db_session)

    assert deleted == 1
    await db_session.rollback()  # only a committed delete survives this
    assert await remaining_token_ids(db_session) == {valid_id}


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
    # rollback() below expires the objects, so read the jti values now.
    kept = {tokens["valid"].jti, tokens["revoked_not_expired"].jti}

    deleted = await delete_expired_refresh_tokens(db_session)

    assert deleted == 2
    await db_session.rollback()  # only a committed delete survives this
    remaining = set(await db_session.scalars(select(RefreshToken.jti)))
    assert remaining == kept


async def test_set_password_affects_only_this_user(db_session: AsyncSession) -> None:
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    users = {}
    for name in ("target", "other"):
        user, _ = await create_user(db_session, f"{name}@example.com", PASSWORD)
        db_session.add_all(
            [
                RefreshToken(user_id=user.id, jti=name[0] * 32, expires_at=future),
                PasswordResetToken(
                    user_id=user.id, token=name[0] * 64, expires_at=future
                ),
            ]
        )
        users[name] = user
    await db_session.commit()

    target_id, other_id = users["target"].id, users["other"].id

    await set_password(db_session, users["target"], NEW_PASSWORD)

    db_session.expire_all()  # read the result from the DB, not from memory
    target = await get_user_by_id(db_session, target_id)
    other = await get_user_by_id(db_session, other_id)
    assert verify_password(NEW_PASSWORD, target.hashed_password)
    assert verify_password(PASSWORD, other.hashed_password)
    revoked = dict(
        (
            await db_session.execute(select(RefreshToken.user_id, RefreshToken.revoked))
        ).all()
    )
    assert revoked == {target.id: True, other.id: False}
    reset_owners = set(await db_session.scalars(select(PasswordResetToken.user_id)))
    assert reset_owners == {other.id}


async def test_refresh_activation_token_returns_new_token_when_old_is_loaded(
    db_session: AsyncSession,
) -> None:
    # The upsert keeps the row id; the old object already in the session
    # must not be returned, or the email would carry the dead token.
    old = await create_user_with_token(db_session, "user@example.com", expired=False)
    old_value = old.token

    new = await refresh_activation_token(db_session, old.user)

    stored = await db_session.scalar(
        select(ActivationToken.token).execution_options(populate_existing=True)
    )
    assert new.token == stored != old_value


async def test_delete_expired_password_reset_tokens_keeps_valid_ones(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(timezone.utc)
    expires = {
        "expired@example.com": now - timedelta(minutes=1),
        "valid@example.com": now + timedelta(minutes=30),
    }
    for i, (email, expires_at) in enumerate(expires.items()):
        user, _ = await create_user(db_session, email, PASSWORD)
        db_session.add(
            PasswordResetToken(
                user_id=user.id, token=str(i) * 64, expires_at=expires_at
            )
        )
    await db_session.commit()

    deleted = await delete_expired_password_reset_tokens(db_session)

    assert deleted == 1
    await db_session.rollback()  # only a committed delete survives this
    remaining = list(await db_session.scalars(select(PasswordResetToken.token)))
    assert remaining == ["1" * 64]
    assert await db_session.scalar(select(func.count()).select_from(User)) == 2
