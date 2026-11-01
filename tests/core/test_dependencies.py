"""require_group: the role hierarchy ADMIN > MODERATOR > USER.

The check is called directly with an in-memory user (no HTTP, no DB):
only the comparison of group levels is tested here.
"""

import pytest
from fastapi import HTTPException

from app.core.dependencies import PERMISSION_DENIED, require_group
from app.models.accounts import User, UserGroup, UserGroupEnum

USER = UserGroupEnum.USER
MODERATOR = UserGroupEnum.MODERATOR
ADMIN = UserGroupEnum.ADMIN


def make_user(group: UserGroupEnum) -> User:
    return User(email="user@example.com", group=UserGroup(name=group))


@pytest.mark.parametrize(
    "user_group, required_group",
    [
        (USER, USER),
        (MODERATOR, USER),
        (ADMIN, USER),
        (MODERATOR, MODERATOR),
        (ADMIN, MODERATOR),
        (ADMIN, ADMIN),
    ],
)
async def test_require_group_allows_same_or_higher_group(
    user_group: UserGroupEnum, required_group: UserGroupEnum
) -> None:
    user = make_user(user_group)

    assert await require_group(required_group)(user) is user


@pytest.mark.parametrize(
    "user_group, required_group",
    [(USER, MODERATOR), (USER, ADMIN), (MODERATOR, ADMIN)],
)
async def test_require_group_denies_lower_group(
    user_group: UserGroupEnum, required_group: UserGroupEnum
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await require_group(required_group)(make_user(user_group))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == PERMISSION_DENIED
