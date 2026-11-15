"""Shared helpers for the payment tests."""

import hashlib
import hmac
import json
import time

from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.payments import Payment, PaymentItem


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
