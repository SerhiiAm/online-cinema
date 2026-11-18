"""Shared helpers for the payment tests."""

import hashlib
import hmac
import json
import time
from datetime import datetime

from httpx import AsyncClient, Response
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.orders import Order, OrderStatusEnum
from app.models.payments import Payment, PaymentItem, PaymentStatusEnum
from tests.routers.movies.helpers import auth
from tests.routers.orders.helpers import save_order


def sign(
    payload: str,
    timestamp: int | None = None,
    secret: str = settings.stripe_webhook_secret,
) -> str:
    """A `Stripe-Signature` header made the way Stripe makes it."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def session_event(
    order_id: int | None,
    *,
    event_type: str = "checkout.session.completed",
    payment_status: str = "paid",
    amount_total: int = 999,
    currency: str = "pln",
    client_reference_id: str | None = None,
    payment_intent: str = "pi_test_1",
) -> str:
    """The body of a Stripe event about a Checkout session of the order.

    `client_reference_id` replaces the order id given to Stripe, for
    sessions that are not ours."""
    if client_reference_id is None and order_id is not None:
        client_reference_id = str(order_id)
    return json.dumps(
        {
            "id": "evt_test_1",
            "object": "event",
            "type": event_type,
            "data": {
                "object": {
                    "id": "cs_test_1",
                    "object": "checkout.session",
                    "client_reference_id": client_reference_id,
                    "payment_status": payment_status,
                    "amount_total": amount_total,
                    "currency": currency,
                    "payment_intent": payment_intent,
                }
            },
        }
    )


async def send_webhook(
    client: AsyncClient, payload: str, signature: str | None = None
) -> Response:
    """Posts the payload as Stripe would; signed properly unless a
    `signature` is given ("" sends no signature at all)."""
    if signature is None:
        signature = sign(payload)
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["Stripe-Signature"] = signature
    return await client.post("/payments/webhook", content=payload, headers=headers)


async def get_payments(db: AsyncSession) -> list[Payment]:
    await db.rollback()  # see what other sessions committed
    return list(await db.scalars(select(Payment).order_by(Payment.id)))


async def get_payment_items(db: AsyncSession) -> list[PaymentItem]:
    await db.rollback()
    return list(await db.scalars(select(PaymentItem).order_by(PaymentItem.id)))


async def save_payment(
    db: AsyncSession,
    user_id: int,
    movie_id: int,
    status: PaymentStatusEnum = PaymentStatusEnum.SUCCESSFUL,
    created_at: str | None = None,
) -> int:
    """Saves a paid order of one movie (9.99) and its payment straight into
    the DB; `created_at` (ISO) dates the payment. Returns the payment id.

    For states the API can't produce (other statuses, past dates)."""
    order_id = await save_order(db, user_id, [movie_id], OrderStatusEnum.PAID)
    amount = await db.scalar(select(Order.total_amount).where(Order.id == order_id))
    values = {
        "user_id": user_id,
        "order_id": order_id,
        "amount": amount,
        "status": status,
    }
    if created_at is not None:
        values["created_at"] = datetime.fromisoformat(created_at)
    payment_id = await db.scalar(insert(Payment).values(values).returning(Payment.id))
    await db.commit()
    return payment_id


async def list_payments(client: AsyncClient, token: str, **params: str) -> Response:
    return await client.get("/payments", params=params, headers=auth(token))


async def list_all_payments(
    client: AsyncClient, token: str, **filters: str
) -> Response:
    return await client.get("/payments/all", params=filters, headers=auth(token))
