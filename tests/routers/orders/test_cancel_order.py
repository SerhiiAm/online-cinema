"""POST /orders/{order_id}/cancel: the user cancels an unpaid order."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.dependencies import MAX_DB_ID
from app.models.orders import Order, OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL
from tests.routers.carts.helpers import add_to_cart, cart_names, get_cart
from tests.routers.orders.helpers import (
    cancel_order,
    create_order,
    order_names,
    save_order,
    status_of,
    user_id_of,
)

NOT_FOUND = {"detail": "Order not found."}
PAID = {"detail": "A paid order can't be canceled."}
ALREADY_CANCELED = {"detail": "The order is already canceled."}


async def test_pending_order_is_canceled(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Tenet"])
    created = (await create_order(client, user_token)).json()

    response = await cancel_order(client, user_token, created["id"])

    assert response.status_code == 200
    # The same order as "create" returned, only the status is different.
    del created["excluded"]
    assert response.json() == {**created, "status": "canceled"}
    assert await status_of(db_session, created["id"]) == OrderStatusEnum.CANCELED
    # The cart is left as it was.
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Inception", "Tenet"]


async def test_without_token_is_401(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])

    response = await client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 401
    assert await status_of(db_session, order_id) == OrderStatusEnum.PENDING


@pytest.mark.parametrize(
    ("status", "error"),
    [(OrderStatusEnum.PAID, PAID), (OrderStatusEnum.CANCELED, ALREADY_CANCELED)],
    ids=["paid", "already canceled"],
)
async def test_order_that_is_not_pending_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    status: OrderStatusEnum,
    error: dict[str, str],
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]], status)

    response = await cancel_order(client, user_token, order_id)

    assert response.status_code == 409
    assert response.json() == error
    assert await status_of(db_session, order_id) == status


async def test_order_of_another_user_is_404(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    other_id = await user_id_of(db_session, OTHER_EMAIL)
    order_id = await save_order(db_session, other_id, [movies["Inception"]])

    response = await cancel_order(client, user_token, order_id)

    # The same answer as for an order that doesn't exist.
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert await status_of(db_session, order_id) == OrderStatusEnum.PENDING


async def test_unknown_order_is_404(client: AsyncClient, user_token: str) -> None:
    response = await cancel_order(client, user_token, MAX_DB_ID)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


async def test_order_id_out_of_db_range_is_422(
    client: AsyncClient, user_token: str
) -> None:
    response = await cancel_order(client, user_token, MAX_DB_ID + 1)

    assert response.status_code == 422


async def test_only_that_order_is_canceled(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    other_id = await user_id_of(db_session, OTHER_EMAIL)
    mine = await save_order(db_session, user_id, [movies["Inception"]])
    my_second = await save_order(db_session, user_id, [movies["Tenet"]])
    theirs = await save_order(db_session, other_id, [movies["Inception"]])

    await cancel_order(client, user_token, mine)

    assert await status_of(db_session, mine) == OrderStatusEnum.CANCELED
    assert await status_of(db_session, my_second) == OrderStatusEnum.PENDING
    assert await status_of(db_session, theirs) == OrderStatusEnum.PENDING


async def test_cart_can_be_ordered_again_after_cancel(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    first = (await create_order(client, user_token)).json()
    await cancel_order(client, user_token, first["id"])
    await add_to_cart(client, user_token, movies["Tenet"])

    response = await create_order(client, user_token)

    assert response.status_code == 201
    assert response.json()["id"] != first["id"]
    assert order_names(response.json()) == ["Inception", "Tenet"]


# --- Parallel requests ---


async def test_double_click_cancels_once(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])

    responses = await asyncio.gather(
        cancel_order(client, user_token, order_id),
        cancel_order(client, user_token, order_id),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert await status_of(db_session, order_id) == OrderStatusEnum.CANCELED


async def test_order_paid_at_the_same_moment_stays_paid(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # The payment has changed the status but is not committed yet: "cancel"
    # still sees a pending order. It must wait and then refuse, not overwrite
    # "paid" with "canceled".
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    async with async_sessionmaker(db_engine)() as payment:
        await payment.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(status=OrderStatusEnum.PAID)
        )
        canceling = asyncio.create_task(cancel_order(client, user_token, order_id))
        await asyncio.sleep(0.5)
        assert not canceling.done()
        await payment.commit()

    response = await canceling

    assert response.status_code == 409
    assert response.json() == PAID
    assert await status_of(db_session, order_id) == OrderStatusEnum.PAID
