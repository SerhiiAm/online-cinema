"""POST /cart/items: a movie the user has already bought is refused."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orders import OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL
from tests.routers.carts.helpers import (
    add_to_cart,
    cart_names,
    count_cart_rows,
    get_cart,
)
from tests.routers.orders.helpers import save_order, user_id_of

ALREADY_PURCHASED = {"detail": "You have already bought this movie."}


async def test_purchased_movie_is_409_and_saves_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Inception"]], OrderStatusEnum.PAID)

    response = await add_to_cart(client, user_token, movies["Inception"])

    assert response.status_code == 409
    assert response.json() == ALREADY_PURCHASED
    # Not even an empty cart is left behind.
    assert await count_cart_rows(db_session) == {"carts": 0, "items": 0}


async def test_other_movies_can_still_be_added(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Inception"]], OrderStatusEnum.PAID)

    response = await add_to_cart(client, user_token, movies["Tenet"])

    assert response.status_code == 201
    assert cart_names(response.json()) == ["Tenet"]


@pytest.mark.parametrize(
    "status",
    [OrderStatusEnum.PENDING, OrderStatusEnum.CANCELED],
    ids=["unpaid order", "canceled order"],
)
async def test_movie_in_an_order_that_is_not_paid_can_be_added(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    status: OrderStatusEnum,
) -> None:
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Inception"]], status)

    response = await add_to_cart(client, user_token, movies["Inception"])

    assert response.status_code == 201
    assert cart_names(response.json()) == ["Inception"]


async def test_purchase_of_another_user_does_not_count(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    other_id = await user_id_of(db_session, OTHER_EMAIL)
    await save_order(db_session, other_id, [movies["Inception"]], OrderStatusEnum.PAID)

    response = await add_to_cart(client, user_token, movies["Inception"])

    assert response.status_code == 201


async def test_purchased_movie_that_is_in_the_cart_is_reported_as_purchased(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # In the cart since before the payment: "bought" is the answer that
    # tells the user why adding makes no sense.
    await add_to_cart(client, user_token, movies["Inception"])
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Inception"]], OrderStatusEnum.PAID)

    response = await add_to_cart(client, user_token, movies["Inception"])

    assert response.status_code == 409
    assert response.json() == ALREADY_PURCHASED
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Inception"]
