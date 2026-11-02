from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.accounts import create_user, get_user_by_id
from app.models.accounts import UserGroupEnum
from app.scripts.make_admin import make_admin

PASSWORD = "Str0ng!pass"


async def group_of(db: AsyncSession, user_id: int) -> UserGroupEnum:
    db.expire_all()  # read the result from the DB, not from memory
    user = await get_user_by_id(db, user_id)
    assert user is not None
    return user.group.name


async def test_make_admin_promotes_user_with_given_email(
    db_session: AsyncSession,
) -> None:
    target, _ = await create_user(db_session, "boss@example.com", PASSWORD)
    other, _ = await create_user(db_session, "other@example.com", PASSWORD)
    target_id, other_id = target.id, other.id

    assert await make_admin(db_session, "boss@example.com") is True

    assert await group_of(db_session, target_id) == UserGroupEnum.ADMIN
    assert await group_of(db_session, other_id) == UserGroupEnum.USER


async def test_make_admin_unknown_email_changes_nothing(
    db_session: AsyncSession,
) -> None:
    user, _ = await create_user(db_session, "user@example.com", PASSWORD)
    user_id = user.id

    assert await make_admin(db_session, "missing@example.com") is False

    assert await group_of(db_session, user_id) == UserGroupEnum.USER
