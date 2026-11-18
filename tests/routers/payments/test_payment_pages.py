"""GET /payments/success and /payments/cancel: Stripe returns the browser."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import stripe
from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID
from app.models.movies import Movie
from app.models.orders import Order, OrderStatusEnum
from tests.routers.carts.helpers import add_to_cart, cart_names, get_cart
from tests.routers.conftest import FakeStripe
from tests.routers.orders.helpers import (
    checkout,
    save_order,
    status_of,
    user_id_of,
)
from tests.routers.payments.helpers import get_payments, send_webhook, session_event

NOT_FOUND = {"detail": "Payment session not found."}
UNAVAILABLE = {"detail": "The payment service is unavailable. Try again later."}


@pytest.fixture
async def movies(db_session: AsyncSession, movies: dict[str, int]) -> dict[str, int]:
    """All at 9.99, the price of save_order, so its orders can be paid."""
    await db_session.execute(update(Movie).values(price="9.99"))
    await db_session.commit()
    return movies


@pytest.fixture
async def order(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    fake_stripe: FakeStripe,
) -> int:
    """Inception + Tenet (19.98) with an open Stripe page cs_test_1; the
    cart has Memento too."""
    for name in ["Inception", "Memento", "Tenet"]:
        await add_to_cart(client, user_token, movies[name])
    user_id = await user_id_of(db_session)
    order_id = await save_order(
        db_session, user_id, [movies["Inception"], movies["Tenet"]]
    )
    await checkout(client, user_token, order_id)
    return order_id


async def success_page(client: AsyncClient, session_id: str) -> Response:
    return await client.get("/payments/success", params={"session_id": session_id})


async def test_paid_order_is_confirmed_even_before_the_webhook(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    fake_stripe: FakeStripe,
    send_payment_email_mock: MagicMock,
) -> None:
    fake_stripe.pay("cs_test_1")

    # No token: the browser comes from Stripe's page.
    response = await success_page(client, "cs_test_1")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order,
        "status": "paid",
        "message": f"Thank you! Order #{order} is paid. "
        "A confirmation email is on its way.",
    }
    assert await status_of(db_session, order) == OrderStatusEnum.PAID
    [payment] = await get_payments(db_session)
    assert payment.external_payment_id == "pi_test_1"
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Memento"]
    send_payment_email_mock.assert_called_once()


@pytest.mark.parametrize("first", ["webhook", "page"])
async def test_page_and_webhook_record_one_payment(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    fake_stripe: FakeStripe,
    send_payment_email_mock: MagicMock,
    first: str,
) -> None:
    fake_stripe.pay("cs_test_1")
    webhook = session_event(order, amount_total=1998)

    if first == "webhook":
        await send_webhook(client, webhook)
        response = await success_page(client, "cs_test_1")
    else:
        response = await success_page(client, "cs_test_1")
        await send_webhook(client, webhook)

    assert response.json()["status"] == "paid"
    assert len(await get_payments(db_session)) == 1
    send_payment_email_mock.assert_called_once()


async def test_unpaid_session_shows_pending(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    send_payment_email_mock: MagicMock,
) -> None:
    # E.g. the user opens the address before paying.
    response = await success_page(client, "cs_test_1")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order,
        "status": "pending",
        "message": f"The payment for order #{order} is not confirmed yet. "
        "Check the status of the order a little later.",
    }
    assert await get_payments(db_session) == []
    send_payment_email_mock.assert_not_called()


async def test_paid_but_canceled_order_says_so(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    fake_stripe: FakeStripe,
) -> None:
    # Our cancel closes the page first, so only a change made by hand in the
    # DB can lead here; the user still gets a clear answer.
    fake_stripe.pay("cs_test_1")
    await db_session.execute(
        update(Order).where(Order.id == order).values(status=OrderStatusEnum.CANCELED)
    )
    await db_session.commit()

    response = await success_page(client, "cs_test_1")

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order,
        "status": "canceled",
        "message": f"Order #{order} is canceled. If you were charged, "
        "contact support.",
    }
    assert await get_payments(db_session) == []


async def test_unknown_session_is_404(
    client: AsyncClient, fake_stripe: FakeStripe
) -> None:
    response = await success_page(client, "cs_test_unknown")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


async def test_session_that_is_not_ours_is_404(
    client: AsyncClient, fake_stripe: FakeStripe, caplog: pytest.LogCaptureFixture
) -> None:
    # A paid session made outside our shop (e.g. in the Stripe Dashboard).
    fake_stripe.sessions["cs_test_other"] = SimpleNamespace(
        id="cs_test_other",
        status="complete",
        payment_status="paid",
        client_reference_id=None,
        amount_total=999,
        currency="pln",
        payment_intent="pi_test_other",
    )

    response = await success_page(client, "cs_test_other")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    # Turned away before any fulfilling is tried: nothing to report.
    assert caplog.records == []


async def test_session_of_a_deleted_order_is_404(
    client: AsyncClient, fake_stripe: FakeStripe
) -> None:
    fake_stripe.sessions["cs_test_gone"] = SimpleNamespace(
        id="cs_test_gone",
        status="complete",
        payment_status="paid",
        client_reference_id=str(MAX_DB_ID),
        amount_total=999,
        currency="pln",
        payment_intent="pi_test_gone",
    )

    response = await success_page(client, "cs_test_gone")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


async def test_stripe_error_is_502(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    fake_stripe: FakeStripe,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_stripe.pay("cs_test_1")
    fake_stripe.error = stripe.APIConnectionError("Stripe is down")

    response = await success_page(client, "cs_test_1")

    assert response.status_code == 502
    assert response.json() == UNAVAILABLE
    assert "Stripe request failed for session cs_test_1" in caplog.text
    assert "Stripe is down" in caplog.text
    assert await status_of(db_session, order) == OrderStatusEnum.PENDING


@pytest.mark.parametrize(
    "session_id",
    ["", "abc", "cs_", "pi_test_1", "cs_test 1", "cs_test\x001", "cs_" + "a" * 64],
    ids=["empty", "text", "prefix only", "other id", "space", "NUL", "67 chars"],
)
async def test_malformed_session_id_is_422_without_asking_stripe(
    client: AsyncClient, session_id: str
) -> None:
    # No fake_stripe: a call to Stripe would fail the test.
    response = await success_page(client, session_id)

    assert response.status_code == 422


async def test_longest_session_id_is_asked(
    client: AsyncClient, fake_stripe: FakeStripe
) -> None:
    # 66 characters is Stripe's own limit.
    response = await success_page(client, "cs_" + "a" * 63)

    assert response.status_code == 404


async def test_session_id_is_required(client: AsyncClient) -> None:
    response = await client.get("/payments/success")

    assert response.status_code == 422


# --- /payments/cancel ---


async def test_cancel_page_suggests_another_try(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
) -> None:
    response = await client.get("/payments/cancel", params={"order_id": order})

    assert response.status_code == 200
    assert response.json() == {
        "order_id": order,
        "message": "The payment was not completed. "
        f"Order #{order} is still waiting for payment: "
        "try again or use a different payment method.",
    }
    assert await status_of(db_session, order) == OrderStatusEnum.PENDING


@pytest.mark.parametrize(
    "params",
    [{}, {"order_id": "abc"}, {"order_id": 0}, {"order_id": MAX_DB_ID + 1}],
    ids=["missing", "text", "zero", "too big"],
)
async def test_cancel_page_with_bad_order_id_is_422(
    client: AsyncClient, params: dict
) -> None:
    response = await client.get("/payments/cancel", params=params)

    assert response.status_code == 422
