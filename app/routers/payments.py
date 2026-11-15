import logging
from typing import Annotated

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from kombu.exceptions import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import payment_gateway as gateway
from app.core.database import get_db
from app.core.dependencies import MAX_DB_ID
from app.crud import payments as crud
from app.crud.orders import OrderNotFoundError, OrderNotPendingError
from app.schemas.payments import WebhookResponse
from app.tasks.email import send_payment_email

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]

INVALID_WEBHOOK = "The request is not a valid Stripe event."
# The events after which Stripe may have the money. "completed" comes when
# the customer finishes the page; with a delayed payment method (a bank
# debit) the money isn't there yet then, and "async_payment_succeeded"
# comes later. Every other event needs nothing from us.
PAYMENT_EVENTS = {
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}


def queue_payment_email(paid: crud.PaidOrder) -> None:
    # The order is already paid, so a broker outage must not turn into an
    # error: Stripe would then send the event again for nothing.
    try:
        send_payment_email.delay(
            paid.email,
            paid.order_id,
            f"{paid.amount} {gateway.CURRENCY.upper()}",
            paid.movie_names,
        )
    except OperationalError:
        logger.exception("Failed to queue payment email for order %s", paid.order_id)


def _order_id_of(session: stripe.checkout.Session) -> int | None:
    """Our order id from the session; None if the session is not ours
    (e.g. made in the Stripe Dashboard or by `stripe trigger`)."""
    reference = session.client_reference_id
    if reference is None or not reference.isdecimal():
        return None
    order_id = int(reference)
    return order_id if 1 <= order_id <= MAX_DB_ID else None


async def fulfill_checkout(db: AsyncSession, session: stripe.checkout.Session) -> None:
    """Marks the session's order paid if Stripe has the money.

    Safe to run again for the same session: Stripe may send an event twice.
    A problem that a repeat can't fix (an order that is not ours, a wrong
    amount, a canceled order) is only logged: such money has to be sorted
    out by a person in the Stripe Dashboard.
    """
    if session.payment_status != "paid":
        return  # a delayed payment: wait for "async_payment_succeeded"
    order_id = _order_id_of(session)
    if order_id is None:
        logger.warning("Paid Checkout session %s has no order of ours", session.id)
        return
    if session.currency != gateway.CURRENCY:
        logger.error(
            "Order %s was paid in %s (session %s)",
            order_id,
            session.currency,
            session.id,
        )
        return
    amount = gateway.from_minor_units(session.amount_total)
    try:
        paid = await crud.pay_order(db, order_id, amount, session.payment_intent)
    except OrderNotFoundError:
        logger.error("Session %s was paid for unknown order %s", session.id, order_id)
        return
    except OrderNotPendingError as exc:
        logger.error(
            "Order %s is %s but was paid (session %s)",
            order_id,
            exc.status.value,
            session.id,
        )
        return
    except crud.PaymentAmountMismatchError:
        logger.error(
            "Order %s was paid %s, not its total (session %s)",
            order_id,
            amount,
            session.id,
        )
        return
    if paid is not None:  # None: this payment was already recorded
        queue_payment_email(paid)


@router.post(
    "/webhook",
    summary="Stripe webhook (called by Stripe only)",
    description=(
        "Stripe calls this address after a payment; it is not meant for "
        "users. The request must carry Stripe's signature (the "
        "`Stripe-Signature` header, made with our webhook secret). When the "
        "money is received, the order becomes `paid`, the payment is "
        "saved, the order's movies leave the cart and a confirmation email "
        "is sent. The same event sent again changes nothing."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "The signature is missing, wrong or older than "
            "5 minutes, or the body is not a Stripe event."
        },
    },
)
async def stripe_webhook(
    request: Request,
    db: DbSession,
    stripe_signature: Annotated[str, Header()] = "",
) -> WebhookResponse:
    # The raw body: the signature is made over the exact bytes Stripe sent.
    payload = await request.body()
    try:
        event = gateway.parse_webhook_event(payload, stripe_signature)
    except gateway.InvalidWebhookError as exc:
        logger.warning("Rejected a webhook request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_WEBHOOK
        )
    if event.type in PAYMENT_EVENTS:
        await fulfill_checkout(db, event.data.object)
    # Any answer outside 2xx makes Stripe send the event again later;
    # a database error (500) should, everything else shouldn't.
    return WebhookResponse()
