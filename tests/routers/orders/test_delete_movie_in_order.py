"""DELETE /movies/{id} for a movie that is in users' orders."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.orders import Order, OrderItem, OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL, get_user
from tests.routers.carts.helpers import add_to_cart, cart_names, get_cart
from tests.routers.movies.helpers import delete_movie, movie_exists
from tests.routers.orders.helpers import count_order_rows, save_order

IN_ORDERS = {"detail": "This movie is in users' orders and can't be deleted."}


async def other_user_id(db: AsyncSession) -> int:
    return (await get_user(db, OTHER_EMAIL)).id


@pytest.mark.parametrize("status", list(OrderStatusEnum), ids=lambda s: s.value)
async def test_movie_in_an_order_is_409_even_with_force(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
    status: OrderStatusEnum,
) -> None:
    user_id = await other_user_id(db_session)
    await save_order(db_session, user_id, [movies["Inception"]], status)

    response = await delete_movie(
        client, moderator_token, movies["Inception"], force=True
    )

    assert response.status_code == 409
    assert response.json() == IN_ORDERS
    assert await movie_exists(db_session, movies["Inception"])
    assert await count_order_rows(db_session) == {"orders": 1, "items": 1}


async def test_order_is_reported_before_the_cart(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    # In a cart AND in an order, no force: the answer must not be "send
    # force=true", because force would be refused as well.
    await add_to_cart(client, other_token, movies["Inception"])
    user_id = await other_user_id(db_session)
    await save_order(db_session, user_id, [movies["Inception"]])

    response = await delete_movie(client, moderator_token, movies["Inception"])

    assert response.status_code == 409
    assert response.json() == IN_ORDERS
    assert cart_names((await get_cart(client, other_token)).json()) == ["Inception"]


async def test_movie_not_in_orders_is_deleted(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    # Another movie in an order must not block this one.
    user_id = await other_user_id(db_session)
    await save_order(db_session, user_id, [movies["Tenet"]])

    response = await delete_movie(client, moderator_token, movies["Inception"])

    assert response.status_code == 204
    assert not await movie_exists(db_session, movies["Inception"])


async def test_delete_waits_for_an_order_in_progress(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    # An order is saved but not committed yet when the moderator deletes.
    # Without the row lock the delete wouldn't see that order and the DB
    # itself would refuse the delete: a 500 instead of a 409.
    user_id = await other_user_id(db_session)
    async with async_sessionmaker(db_engine)() as ordering:
        order_id = await ordering.scalar(
            insert(Order).values(user_id=user_id, total_amount=1).returning(Order.id)
        )
        await ordering.execute(
            insert(OrderItem).values(
                order_id=order_id, movie_id=movies["Inception"], price_at_order=1
            )
        )
        deleting = asyncio.create_task(
            delete_movie(client, moderator_token, movies["Inception"], force=True)
        )
        await asyncio.sleep(0.5)
        assert not deleting.done()  # waits for the order to be saved
        await ordering.commit()

    response = await deleting

    assert response.status_code == 409
    assert response.json() == IN_ORDERS
    assert await movie_exists(db_session, movies["Inception"])
