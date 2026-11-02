"""Admin-only endpoints: changing a user's group."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import PERMISSION_DENIED
from app.crud.accounts import promote_to_admin
from app.models.accounts import User, UserGroupEnum
from app.routers.accounts import ADMIN_GROUP_LOCKED, USER_NOT_FOUND
from tests.routers.accounts.helpers import (
    EMAIL,
    change_group,
    get_group,
    get_user,
    login,
    register,
    set_group,
)

ADMIN_EMAIL = "admin@example.com"


@pytest.fixture
async def admin(client: AsyncClient, db_session: AsyncSession) -> User:
    await register(client, ADMIN_EMAIL)
    user = await get_user(db_session, ADMIN_EMAIL)
    await promote_to_admin(db_session, user)
    return user


@pytest.fixture
async def admin_token(client: AsyncClient, admin: User) -> str:
    response = await login(client, ADMIN_EMAIL)
    return response.json()["access_token"]


@pytest.mark.parametrize(
    ("old_group", "new_group"),
    [
        (UserGroupEnum.USER, UserGroupEnum.MODERATOR),
        (UserGroupEnum.MODERATOR, UserGroupEnum.USER),
    ],
)
async def test_admin_changes_user_group(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    admin_token: str,
    old_group: UserGroupEnum,
    new_group: UserGroupEnum,
) -> None:
    await set_group(db_session, old_group)

    response = await change_group(
        client, admin_token, active_user.id, new_group.value
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": active_user.id,
        "email": EMAIL,
        "is_active": True,
        "group": new_group.value,
    }
    assert await get_group(db_session) == new_group


async def test_admin_group_cannot_be_assigned(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    admin_token: str,
) -> None:
    response = await change_group(client, admin_token, active_user.id, "admin")

    assert response.status_code == 422
    assert await get_group(db_session) == UserGroupEnum.USER


@pytest.mark.parametrize("target", ["self", "other admin"])
async def test_admin_accounts_cannot_be_changed(
    client: AsyncClient,
    db_session: AsyncSession,
    active_user: User,
    admin: User,
    admin_token: str,
    target: str,
) -> None:
    if target == "other admin":
        await set_group(db_session, UserGroupEnum.ADMIN)
        target_id, target_email = active_user.id, EMAIL
    else:
        target_id, target_email = admin.id, ADMIN_EMAIL

    response = await change_group(client, admin_token, target_id, "user")

    assert response.status_code == 403
    assert response.json() == {"detail": ADMIN_GROUP_LOCKED}
    assert await get_group(db_session, target_email) == UserGroupEnum.ADMIN


async def test_change_group_of_unknown_user_returns_404(
    client: AsyncClient, admin_token: str
) -> None:
    response = await change_group(client, admin_token, 999, "moderator")

    assert response.status_code == 404
    assert response.json() == {"detail": USER_NOT_FOUND}


@pytest.mark.parametrize("caller_group", [UserGroupEnum.USER, UserGroupEnum.MODERATOR])
async def test_only_admin_can_change_groups(
    client: AsyncClient,
    db_session: AsyncSession,
    token_pair: dict,
    caller_group: UserGroupEnum,
) -> None:
    other_email = "other@example.com"
    await register(client, other_email)
    other = await get_user(db_session, other_email)
    await set_group(db_session, caller_group)

    response = await change_group(
        client, token_pair["access_token"], other.id, "moderator"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": PERMISSION_DENIED}
    assert await get_group(db_session, other_email) == UserGroupEnum.USER
