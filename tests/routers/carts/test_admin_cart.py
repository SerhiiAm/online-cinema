from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID
from app.models.accounts import UserGroupEnum
from tests.routers.accounts.helpers import OTHER_EMAIL, get_user, set_group
from tests.routers.carts.helpers import (
    add_to_cart,
    cart_names,
    get_user_cart,
)

EMPTY_CART = {"items": [], "total": "0"}


async def other_user_id(db: AsyncSession) -> int:
    return (await get_user(db, OTHER_EMAIL)).id


async def test_admin_sees_users_cart(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, other_token, movies["Tenet"])
    await add_to_cart(client, other_token, movies["Inception"])
    # The admin's own cart must not show up instead.
    await add_to_cart(client, admin_token, movies["Memento"])

    response = await get_user_cart(client, admin_token, await other_user_id(db_session))

    assert response.status_code == 200
    cart = response.json()
    assert cart_names(cart) == ["Tenet", "Inception"]
    assert cart["total"] == "27.49"  # 7.50 + 19.99


async def test_user_without_cart_has_empty_one(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    other_token: str,
) -> None:
    response = await get_user_cart(client, admin_token, await other_user_id(db_session))

    assert response.status_code == 200
    assert response.json() == EMPTY_CART


async def test_unknown_user_is_404(client: AsyncClient, admin_token: str) -> None:
    response = await get_user_cart(client, admin_token, MAX_DB_ID)

    assert response.status_code == 404
    assert response.json() == {"detail": "User not found."}


async def test_user_id_above_db_integer_is_422(
    client: AsyncClient, admin_token: str
) -> None:
    response = await get_user_cart(client, admin_token, MAX_DB_ID + 1)

    assert response.status_code == 422


async def test_plain_user_is_403(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
) -> None:
    response = await get_user_cart(client, user_token, await other_user_id(db_session))

    assert response.status_code == 403


async def test_moderator_is_403(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
) -> None:
    # Moderators manage content and sales; other users' carts are admin only.
    await set_group(db_session, UserGroupEnum.MODERATOR)

    response = await get_user_cart(client, user_token, await other_user_id(db_session))

    assert response.status_code == 403
