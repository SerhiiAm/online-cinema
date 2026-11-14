"""POST /orders/{order_id}/checkout: the user opens a Stripe payment page."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import stripe
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import MAX_DB_ID
from app.models.movies import Movie
from app.models.orders import Order, OrderItem, OrderStatusEnum
from tests.routers.accounts.helpers import EMAIL, OTHER_EMAIL
from tests.routers.carts.helpers import add_to_cart
from tests.routers.conftest import FakeStripe
from tests.routers.movies.helpers import save_movie
from tests.routers.orders.helpers import (
    cancel_order,
    checkout,
    checkout_session_of,
    create_order,
    save_order,
    status_of,
    user_id_of,
)

NOT_FOUND = {"detail": "Order not found."}
PAID = {"detail": "The order is already paid."}
CANCELED = {"detail": "A canceled order can't be paid. Create a new order."}
PRICES_CHANGED = {
    "detail": "The prices of some movies have changed since the order was "
    "created. Cancel this order and create a new one."
}
TOO_SMALL = {"detail": "The minimum amount for a payment is 2.00 PLN."}
TOO_LARGE = {"detail": "The maximum amount for a payment is 999999.99 PLN."}
IN_PROGRESS = {
    "detail": "The order has already been paid; the confirmation is on its way."
}
UNAVAILABLE = {"detail": "The payment service is unavailable. Try again later."}


@pytest.fixture
async def movies(db_session: AsyncSession, movies: dict[str, int]) -> dict[str, int]:
    """The shared movies, all at 9.99: the price save_order puts into its
    orders, so those orders match the catalog and can be paid."""
    await db_session.execute(update(Movie).values(price="9.99"))
    await db_session.commit()
    return movies


async def set_movie_price(db: AsyncSession, movie_id: int, price: str) -> None:
    await db.execute(update(Movie).where(Movie.id == movie_id).values(price=price))
    await db.commit()


async def set_price(
    db: AsyncSession, order_id: int, movie_id: int, price: str
) -> None:
    """Gives the movie, its line in the order and the order total this price
    (for one-movie orders)."""
    await set_movie_price(db, movie_id, price)
    await db.execute(
        update(OrderItem)
        .where(OrderItem.order_id == order_id)
        .values(price_at_order=price)
    )
    await db.execute(
        update(Order).where(Order.id == order_id).values(total_amount=price)
    )
    await db.commit()


async def test_payment_page_is_opened(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    await set_movie_price(db_session, movies["Inception"], "19.99")
    await set_movie_price(db_session, movies["Tenet"], "7.50")
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Tenet"])
    order_id = (await create_order(client, user_token)).json()["id"]

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.test/1"}
    assert await checkout_session_of(db_session, order_id) == "cs_test_1"
    assert await status_of(db_session, order_id) == OrderStatusEnum.PENDING

    # What was sent to Stripe.
    [sent] = fake_stripe.created
    expires_in = datetime.fromtimestamp(sent.pop("expires_at"), UTC) - datetime.now(
        UTC
    )
    assert timedelta(minutes=59) < expires_in <= timedelta(minutes=60)
    assert sent == {
        "mode": "payment",
        # Prices in grosze, in the order of the order's items.
        "line_items": [
            {
                "price_data": {
                    "currency": "pln",
                    "unit_amount": 1999,
                    "product_data": {"name": "Inception"},
                },
                "quantity": 1,
            },
            {
                "price_data": {
                    "currency": "pln",
                    "unit_amount": 750,
                    "product_data": {"name": "Tenet"},
                },
                "quantity": 1,
            },
        ],
        "client_reference_id": str(order_id),
        "customer_email": EMAIL,
        "success_url": f"{settings.api_url}/payments/success"
        "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": f"{settings.api_url}/payments/cancel?order_id={order_id}",
    }


async def test_open_page_is_returned_again(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    first = await checkout(client, user_token, order_id)

    second = await checkout(client, user_token, order_id)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(fake_stripe.created) == 1
    assert await checkout_session_of(db_session, order_id) == "cs_test_1"


async def test_expired_page_is_replaced(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    await checkout(client, user_token, order_id)
    fake_stripe.sessions["cs_test_1"].status = "expired"

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 200
    assert response.json() == {"checkout_url": "https://checkout.stripe.test/2"}
    assert await checkout_session_of(db_session, order_id) == "cs_test_2"


async def test_page_paid_before_the_webhook_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    await checkout(client, user_token, order_id)
    fake_stripe.sessions["cs_test_1"].status = "complete"

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 409
    assert response.json() == IN_PROGRESS
    # No second page: the order must not be paid twice.
    assert len(fake_stripe.created) == 1
    assert await checkout_session_of(db_session, order_id) == "cs_test_1"


async def test_without_token_is_401(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])

    response = await client.post(f"/orders/{order_id}/checkout")

    assert response.status_code == 401
    assert fake_stripe.created == []


@pytest.mark.parametrize(
    ("status", "error"),
    [(OrderStatusEnum.PAID, PAID), (OrderStatusEnum.CANCELED, CANCELED)],
    ids=["paid", "canceled"],
)
async def test_order_that_is_not_pending_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
    status: OrderStatusEnum,
    error: dict[str, str],
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]], status)

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 409
    assert response.json() == error
    assert fake_stripe.created == []


async def test_order_of_another_user_is_404(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    other_id = await user_id_of(db_session, OTHER_EMAIL)
    order_id = await save_order(db_session, other_id, [movies["Inception"]])

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert fake_stripe.created == []
    assert await checkout_session_of(db_session, order_id) is None


async def test_unknown_order_is_404(
    client: AsyncClient, user_token: str, fake_stripe: FakeStripe
) -> None:
    response = await checkout(client, user_token, MAX_DB_ID)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


async def test_order_id_out_of_db_range_is_422(
    client: AsyncClient, user_token: str, fake_stripe: FakeStripe
) -> None:
    response = await checkout(client, user_token, MAX_DB_ID + 1)

    assert response.status_code == 422


async def test_changed_price_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    user_id = await user_id_of(db_session)
    # Both at 9.99 in the order; only Tenet's price changes, by one grosz.
    order_id = await save_order(
        db_session, user_id, [movies["Inception"], movies["Tenet"]]
    )
    await set_movie_price(db_session, movies["Tenet"], "10.00")

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 409
    assert response.json() == PRICES_CHANGED
    assert fake_stripe.created == []
    # The order itself is not changed.
    assert await status_of(db_session, order_id) == OrderStatusEnum.PENDING


@pytest.mark.parametrize(
    ("price", "code", "error"),
    [
        ("1.99", 409, TOO_SMALL),
        ("2.00", 200, None),
        ("999999.99", 200, None),
        ("1000000.00", 409, TOO_LARGE),
    ],
    ids=["below minimum", "minimum", "maximum", "above maximum"],
)
async def test_amount_limits(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
    price: str,
    code: int,
    error: dict[str, str] | None,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    await set_price(db_session, order_id, movies["Inception"], price)

    response = await checkout(client, user_token, order_id)

    assert response.status_code == code
    if error is None:
        [sent] = fake_stripe.created
        assert sent["line_items"][0]["price_data"]["unit_amount"] == int(
            Decimal(price) * 100
        )
    else:
        assert response.json() == error
        assert fake_stripe.created == []


async def test_order_above_stripe_line_limit_is_one_line(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    refs: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    # Stripe takes at most 100 line items: 100 movies are 100 lines,
    # 101 movies become one line with the order's total.
    movie_ids = [
        await save_movie(db_session, refs, f"Movie {number}", price="9.99")
        for number in range(101)
    ]
    user_id = await user_id_of(db_session)
    hundred = await save_order(db_session, user_id, movie_ids[:100])
    hundred_and_one = await save_order(db_session, user_id, movie_ids)

    await checkout(client, user_token, hundred)
    response = await checkout(client, user_token, hundred_and_one)

    assert response.status_code == 200
    first, second = (sent["line_items"] for sent in fake_stripe.created)
    assert len(first) == 100
    assert first[-1]["price_data"]["product_data"]["name"] == "Movie 99"
    assert second == [
        {
            "price_data": {
                "currency": "pln",
                "unit_amount": 100899,  # 101 x 9.99
                "product_data": {"name": f"Order #{hundred_and_one}: 101 movies"},
            },
            "quantity": 1,
        }
    ]


@pytest.mark.parametrize("step", ["create", "retrieve"])
async def test_stripe_error_is_502(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
    step: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    if step == "retrieve":
        await checkout(client, user_token, order_id)  # a session to look up
    fake_stripe.error = stripe.APIConnectionError("Stripe is down")

    response = await checkout(client, user_token, order_id)

    assert response.status_code == 502
    assert response.json() == UNAVAILABLE
    # The user sees a short text; Stripe's own reason is in the log.
    assert f"Stripe request failed for order {order_id}" in caplog.text
    assert "Stripe is down" in caplog.text
    expected_session = "cs_test_1" if step == "retrieve" else None
    assert await checkout_session_of(db_session, order_id) == expected_session
    # The order's lock is released: canceling it doesn't hang.
    canceled = await asyncio.wait_for(
        cancel_order(client, user_token, order_id), timeout=5
    )
    assert canceled.status_code == 200


# --- Parallel requests ---


async def test_double_click_opens_one_page(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> None:
    # "Stripe" answers slowly, so the second click comes while the first one
    # is still waiting for its page. Two pages would let the order be paid
    # twice.
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])
    fake_stripe.delay = 0.5

    first, second = await asyncio.gather(
        checkout(client, user_token, order_id),
        checkout(client, user_token, order_id),
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(fake_stripe.created) == 1
