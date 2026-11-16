"""POST /orders/{order_id}/cancel: the user cancels an unpaid order."""

import asyncio

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from stripe.checkout import SessionService

from app.core.dependencies import MAX_DB_ID
from app.models.movies import Movie
from app.models.orders import Order, OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL
from tests.routers.carts.helpers import add_to_cart, cart_names, get_cart
from tests.routers.conftest import FakeStripe
from tests.routers.orders.helpers import (
    cancel_order,
    checkout,
    create_order,
    order_names,
    save_order,
    status_of,
    user_id_of,
)
from tests.routers.payments.helpers import send_webhook, session_event

NOT_FOUND = {"detail": "Order not found."}
PAID = {"detail": "A paid order can't be canceled."}
ALREADY_CANCELED = {"detail": "The order is already canceled."}
IN_PROGRESS = {
    "detail": "The order has already been paid; the confirmation is on its way."
}
UNAVAILABLE = {"detail": "The payment service is unavailable. Try again later."}
CANCELED_CANT_BE_PAID = {
    "detail": "A canceled order can't be paid. Create a new order."
}


@pytest.fixture
async def unpaid(
    db_session: AsyncSession, user_token: str, movies: dict[str, int]
) -> int:
    """A pending order of Inception that can be paid (9.99, the catalog
    price), without a payment page yet."""
    await db_session.execute(
        update(Movie).where(Movie.id == movies["Inception"]).values(price="9.99")
    )
    await db_session.commit()
    user_id = await user_id_of(db_session)
    return await save_order(db_session, user_id, [movies["Inception"]])


@pytest.fixture
async def with_page(
    client: AsyncClient, user_token: str, unpaid: int, fake_stripe: FakeStripe
) -> int:
    """The same order with an open Stripe page cs_test_1."""
    await checkout(client, user_token, unpaid)
    return unpaid


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


# --- An order with a Stripe payment page ---


async def test_open_page_is_closed_before_cancel(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    with_page: int,
    fake_stripe: FakeStripe,
) -> None:
    response = await cancel_order(client, user_token, with_page)

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    assert await status_of(db_session, with_page) == OrderStatusEnum.CANCELED
    # The page can't be paid on any more, and no new one is opened.
    assert fake_stripe.sessions["cs_test_1"].status == "expired"
    again = await checkout(client, user_token, with_page)
    assert again.json() == CANCELED_CANT_BE_PAID
    assert len(fake_stripe.created) == 1


async def test_page_that_expired_by_itself_is_canceled(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    with_page: int,
    fake_stripe: FakeStripe,
) -> None:
    fake_stripe.sessions["cs_test_1"].status = "expired"  # after 60 minutes

    response = await cancel_order(client, user_token, with_page)

    assert response.status_code == 200
    assert await status_of(db_session, with_page) == OrderStatusEnum.CANCELED


async def test_order_paid_on_its_page_is_not_canceled(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    with_page: int,
    fake_stripe: FakeStripe,
) -> None:
    # The user has paid, but the webhook hasn't come yet.
    fake_stripe.pay("cs_test_1")

    response = await cancel_order(client, user_token, with_page)

    assert response.status_code == 409
    assert response.json() == IN_PROGRESS
    assert await status_of(db_session, with_page) == OrderStatusEnum.PENDING
    # The order is free again: the webhook that comes next marks it paid.
    await send_webhook(client, session_event(with_page))
    assert await status_of(db_session, with_page) == OrderStatusEnum.PAID


async def test_stripe_error_is_502(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    with_page: int,
    fake_stripe: FakeStripe,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_stripe.error = stripe.APIConnectionError("Stripe is down")

    response = await cancel_order(client, user_token, with_page)

    assert response.status_code == 502
    assert response.json() == UNAVAILABLE
    assert f"Stripe request failed for order {with_page}" in caplog.text
    assert "Stripe is down" in caplog.text
    # Not canceled: the page may still be paid on.
    assert await status_of(db_session, with_page) == OrderStatusEnum.PENDING
    assert fake_stripe.sessions["cs_test_1"].status == "open"


async def test_page_stripe_refuses_to_close_is_502(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    with_page: int,
    fake_stripe: FakeStripe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stripe says "not open", but the page is still open when we ask again.
    async def refuse(session_id: str, params: object = None, options: object = None):
        raise stripe.InvalidRequestError("Refused", None, http_status=400)

    monkeypatch.setattr(SessionService, "expire_async", staticmethod(refuse))

    response = await cancel_order(client, user_token, with_page)

    assert response.status_code == 502
    assert await status_of(db_session, with_page) == OrderStatusEnum.PENDING


async def test_cancel_waits_for_a_page_being_opened(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    unpaid: int,
    fake_stripe: FakeStripe,
) -> None:
    # "Pay" is waiting for Stripe with the order locked. Cancel must wait for
    # it and then close the new page; a cancel that didn't wait would see no
    # page and leave it open for a canceled order.
    fake_stripe.delay = 0.5
    paying = asyncio.create_task(checkout(client, user_token, unpaid))
    await asyncio.sleep(0.2)

    response = await cancel_order(client, user_token, unpaid)

    assert (await paying).status_code == 200
    assert response.status_code == 200
    assert await status_of(db_session, unpaid) == OrderStatusEnum.CANCELED
    assert fake_stripe.sessions["cs_test_1"].status == "expired"
