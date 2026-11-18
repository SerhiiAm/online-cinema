"""POST /payments/webhook: Stripe tells us about a payment."""

import asyncio
import logging
import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from kombu.exceptions import OperationalError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.dependencies import MAX_DB_ID
from app.models.carts import Cart
from app.models.orders import Order, OrderItem, OrderStatusEnum
from app.models.payments import PaymentStatusEnum
from tests.routers.accounts.helpers import EMAIL
from tests.routers.carts.helpers import add_to_cart, cart_names, get_cart
from tests.routers.orders.helpers import save_order, status_of, user_id_of
from tests.routers.payments.helpers import (
    get_payment_items,
    get_payments,
    send_webhook,
    session_event,
    sign,
)

INVALID = {"detail": "The request is not a valid Stripe event."}
RECEIVED = {"received": True}
TOTAL = 1998  # the order below: two movies at 9.99, in grosze
WHOLE_CART = ["Inception", "Memento", "Tenet"]


@pytest.fixture
async def order(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> int:
    """A pending order of Inception + Tenet (9.99 each); the user's cart has
    Memento too."""
    for name in WHOLE_CART:
        await add_to_cart(client, user_token, movies[name])
    user_id = await user_id_of(db_session)
    return await save_order(
        db_session, user_id, [movies["Inception"], movies["Tenet"]]
    )


async def cart_of(client: AsyncClient, token: str) -> list[str]:
    return cart_names((await get_cart(client, token)).json())


async def assert_nothing_paid(
    client: AsyncClient,
    db: AsyncSession,
    token: str,
    order_id: int,
    email_mock: MagicMock,
) -> None:
    assert await status_of(db, order_id) == OrderStatusEnum.PENDING
    assert await get_payments(db) == []
    assert await cart_of(client, token) == WHOLE_CART
    email_mock.assert_not_called()


@pytest.mark.parametrize(
    "event_type",
    ["checkout.session.completed", "checkout.session.async_payment_succeeded"],
)
async def test_payment_marks_order_paid(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
    event_type: str,
) -> None:
    payload = session_event(order, event_type=event_type, amount_total=TOTAL)

    response = await send_webhook(client, payload)

    assert response.status_code == 200
    assert response.json() == RECEIVED
    assert await status_of(db_session, order) == OrderStatusEnum.PAID

    [payment] = await get_payments(db_session)
    assert payment.user_id == await user_id_of(db_session)
    assert payment.order_id == order
    assert payment.amount == Decimal("19.98")
    assert payment.status == PaymentStatusEnum.SUCCESSFUL
    assert payment.external_payment_id == "pi_test_1"
    payment_id = payment.id  # the next read expires the loaded objects
    # One item per order line, at the order's price (the movies cost
    # 19.99 and 7.50 in the catalog now).
    items = await get_payment_items(db_session)
    order_item_ids = list(
        await db_session.scalars(
            select(OrderItem.id)
            .where(OrderItem.order_id == order)
            .order_by(OrderItem.id)
        )
    )
    assert [
        (item.payment_id, item.order_item_id, item.price_at_payment) for item in items
    ] == [(payment_id, item_id, Decimal("9.99")) for item_id in order_item_ids]

    # Only the paid movies leave the cart.
    assert await cart_of(client, user_token) == ["Memento"]
    send_payment_email_mock.assert_called_once_with(
        EMAIL, order, "19.98 PLN", ["Inception", "Tenet"]
    )


def problems_logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


async def test_same_event_again_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    send_payment_email_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = session_event(order, amount_total=TOTAL)
    await send_webhook(client, payload)

    response = await send_webhook(client, payload)

    assert response.status_code == 200
    assert len(await get_payments(db_session)) == 1
    send_payment_email_mock.assert_called_once()
    # A repeated event is normal for Stripe, not a problem to report.
    assert problems_logged(caplog) == []


async def test_two_copies_at_once_make_one_payment(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    send_payment_email_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = session_event(order, amount_total=TOTAL)

    first, second = await asyncio.gather(
        send_webhook(client, payload), send_webhook(client, payload)
    )

    assert first.status_code == second.status_code == 200
    assert len(await get_payments(db_session)) == 1
    send_payment_email_mock.assert_called_once()
    assert problems_logged(caplog) == []


async def test_unpaid_session_is_not_fulfilled(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
) -> None:
    # A delayed payment method: Checkout is finished, the money isn't there.
    payload = session_event(order, payment_status="unpaid", amount_total=TOTAL)

    response = await send_webhook(client, payload)

    assert response.status_code == 200
    await assert_nothing_paid(
        client, db_session, user_token, order, send_payment_email_mock
    )


@pytest.mark.parametrize(
    "event_type",
    [
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
        "payment_intent.succeeded",
    ],
)
async def test_other_events_are_ignored(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
    event_type: str,
) -> None:
    payload = session_event(order, event_type=event_type, amount_total=TOTAL)

    response = await send_webhook(client, payload)

    assert response.status_code == 200
    assert response.json() == RECEIVED
    await assert_nothing_paid(
        client, db_session, user_token, order, send_payment_email_mock
    )


@pytest.mark.parametrize(
    "make_signature",
    [
        lambda payload: "",
        lambda payload: "t=1,v1=abc",
        lambda payload: sign(payload, secret="whsec_someone_else"),
        # Stripe's limit is 5 minutes: an old request can't be replayed.
        lambda payload: sign(payload, timestamp=int(time.time()) - 301),
        # The body was changed after Stripe signed it.
        lambda payload: sign(payload.replace("1998", "1")),
    ],
    ids=["missing", "garbage", "wrong secret", "too old", "body changed"],
)
async def test_request_not_signed_by_stripe_is_400(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
    make_signature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = session_event(order, amount_total=TOTAL)

    response = await send_webhook(client, payload, make_signature(payload))

    assert response.status_code == 400
    assert response.json() == INVALID
    assert "Rejected a webhook request" in caplog.text
    await assert_nothing_paid(
        client, db_session, user_token, order, send_payment_email_mock
    )


async def test_signed_body_that_is_not_json_is_400(client: AsyncClient) -> None:
    response = await send_webhook(client, "not json")

    assert response.status_code == 400
    assert response.json() == INVALID


@pytest.mark.parametrize(
    ("changes", "log"),
    [
        ({"amount_total": TOTAL - 1}, "was paid 19.97, not its total"),
        ({"amount_total": TOTAL, "currency": "eur"}, "was paid in eur"),
    ],
    ids=["wrong amount", "wrong currency"],
)
async def test_payment_that_does_not_match_the_order_is_only_logged(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
    changes: dict,
    log: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 200: sending it again wouldn't help, a person has to sort it out.
    response = await send_webhook(client, session_event(order, **changes))

    assert response.status_code == 200
    assert f"Order {order} {log}" in caplog.text
    await assert_nothing_paid(
        client, db_session, user_token, order, send_payment_email_mock
    )


async def test_payment_for_a_canceled_order_is_only_logged(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    send_payment_email_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(
        db_session, user_id, [movies["Inception"]], OrderStatusEnum.CANCELED
    )

    response = await send_webhook(client, session_event(order_id))

    assert response.status_code == 200
    assert f"Order {order_id} is canceled but was paid" in caplog.text
    assert await status_of(db_session, order_id) == OrderStatusEnum.CANCELED
    assert await get_payments(db_session) == []
    send_payment_email_mock.assert_not_called()


@pytest.mark.parametrize(
    "reference",
    [None, "abc", "0", "-1", " 1", "1.0", str(MAX_DB_ID + 1)],
    ids=["none", "text", "zero", "negative", "space", "fraction", "too big"],
)
async def test_session_that_is_not_ours_is_ignored(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
    reference: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # E.g. a payment made in the Stripe Dashboard or by `stripe trigger`.
    payload = session_event(None, client_reference_id=reference, amount_total=TOTAL)

    response = await send_webhook(client, payload)

    assert response.status_code == 200
    assert "Paid Checkout session cs_test_1 has no order of ours" in caplog.text
    await assert_nothing_paid(
        client, db_session, user_token, order, send_payment_email_mock
    )


async def test_unknown_order_is_only_logged(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    order: int,
    send_payment_email_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = await send_webhook(client, session_event(MAX_DB_ID, amount_total=TOTAL))

    assert response.status_code == 200
    assert f"Session cs_test_1 was paid for unknown order {MAX_DB_ID}" in caplog.text
    await assert_nothing_paid(
        client, db_session, user_token, order, send_payment_email_mock
    )


async def test_user_without_a_cart_is_paid_too(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    order_id = await save_order(db_session, user_id, [movies["Inception"]])

    response = await send_webhook(client, session_event(order_id))

    assert response.status_code == 200
    assert await status_of(db_session, order_id) == OrderStatusEnum.PAID


async def test_other_users_cart_is_not_touched(
    client: AsyncClient,
    db_session: AsyncSession,
    other_token: str,
    order: int,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, other_token, movies["Inception"])

    await send_webhook(client, session_event(order, amount_total=TOTAL))

    assert await cart_of(client, other_token) == ["Inception"]


async def test_email_broker_down_still_pays(
    client: AsyncClient,
    db_session: AsyncSession,
    order: int,
    send_payment_email_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    send_payment_email_mock.side_effect = OperationalError("broker is down")

    response = await send_webhook(client, session_event(order, amount_total=TOTAL))

    assert response.status_code == 200
    assert await status_of(db_session, order) == OrderStatusEnum.PAID
    assert f"Failed to queue payment email for order {order}" in caplog.text


# --- Locks: the payment waits for requests that read the same rows ---


async def test_payment_waits_for_create_order(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    order: int,
) -> None:
    # "Create order" locks the cart while it reads what the user has bought.
    user_id = await user_id_of(db_session)
    async with async_sessionmaker(db_engine)() as other:
        await other.execute(
            select(Cart.id).where(Cart.user_id == user_id).with_for_update()
        )
        task = asyncio.create_task(
            send_webhook(client, session_event(order, amount_total=TOTAL))
        )
        await asyncio.sleep(0.5)
        assert not task.done()
        assert await status_of(db_session, order) == OrderStatusEnum.PENDING
        await other.commit()

    response = await asyncio.wait_for(task, timeout=5)

    assert response.status_code == 200
    assert await status_of(db_session, order) == OrderStatusEnum.PAID


async def test_payment_sees_a_cancel_that_was_in_progress(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    order: int,
    send_payment_email_mock: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The payment must decide on the order as it is after the cancel,
    # not turn the canceled order back into a paid one.
    async with async_sessionmaker(db_engine)() as other:
        await other.execute(
            update(Order)
            .where(Order.id == order)
            .values(status=OrderStatusEnum.CANCELED)
        )
        task = asyncio.create_task(
            send_webhook(client, session_event(order, amount_total=TOTAL))
        )
        await asyncio.sleep(0.5)
        assert not task.done()
        await other.commit()

    response = await asyncio.wait_for(task, timeout=5)

    assert response.status_code == 200
    assert await status_of(db_session, order) == OrderStatusEnum.CANCELED
    assert f"Order {order} is canceled but was paid" in caplog.text
    assert await get_payments(db_session) == []
    send_payment_email_mock.assert_not_called()
