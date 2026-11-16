"""Everything that talks to Stripe. Tests replace these functions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import stripe

from app.core.config import settings
from app.models.orders import Order

CURRENCY = "pln"
# Stripe's limits for one payment in PLN (docs.stripe.com/currencies):
# at least 2.00, and 8 digits in grosze for most payment methods.
MIN_PAYMENT = Decimal("2.00")
MAX_PAYMENT = Decimal("999999.99")
# Stripe accepts 30 minutes to 24 hours; exactly 30 could be refused if our
# clock is a little ahead of Stripe's. Canceling the order closes the page
# anyway, so a longer life costs nothing.
CHECKOUT_LIFETIME = timedelta(minutes=60)
# Stripe takes at most 100 line items in one payment.
MAX_LINE_ITEMS = 100

# Any error from Stripe: refused request, network problem, bad key.
PaymentGatewayError = stripe.StripeError

# 10 s instead of the SDK's 80: the order row stays locked while we wait.
_client = stripe.StripeClient(
    settings.stripe_secret_key, http_client=stripe.HTTPXClient(timeout=10)
)


class InvalidWebhookError(Exception):
    """The request is not a Stripe event signed with our webhook secret."""


def to_minor_units(amount: Decimal) -> int:
    """9.99 -> 999: Stripe takes amounts in grosze, as integers."""
    return int(amount * 100)


def from_minor_units(amount: int) -> Decimal:
    """1998 -> 19.98: Stripe's amounts back in złoty."""
    return Decimal(amount) / 100


def _line_item(name: str, amount: Decimal) -> dict[str, Any]:
    return {
        "price_data": {
            "currency": CURRENCY,
            "unit_amount": to_minor_units(amount),
            "product_data": {"name": name},
        },
        "quantity": 1,
    }


def _line_items(order: Order) -> list[dict[str, Any]]:
    """One line per movie; a bigger order than Stripe allows is shown as one
    line with its total, so it can still be paid."""
    if len(order.items) <= MAX_LINE_ITEMS:
        return [
            _line_item(item.movie.name, item.price_at_order) for item in order.items
        ]
    name = f"Order #{order.id}: {len(order.items)} movies"
    return [_line_item(name, order.total_amount)]


async def create_checkout_session(
    order: Order, email: str
) -> stripe.checkout.Session:
    """Opens a Stripe payment page for the order (its items with movies
    must be loaded)."""
    expires_at = datetime.now(UTC) + CHECKOUT_LIFETIME
    return await _client.v1.checkout.sessions.create_async(
        {
            # Payment methods are not chosen here: Stripe no longer accepts
            # payment_method_types, they are switched on in the Dashboard.
            "mode": "payment",
            "line_items": _line_items(order),
            # The webhook finds the order by it.
            "client_reference_id": str(order.id),
            "customer_email": email,
            "expires_at": int(expires_at.timestamp()),
            # Stripe puts the session id in place of {CHECKOUT_SESSION_ID}.
            "success_url": (
                f"{settings.api_url}/payments/success"
                "?session_id={CHECKOUT_SESSION_ID}"
            ),
            "cancel_url": f"{settings.api_url}/payments/cancel?order_id={order.id}",
        }
    )


async def get_checkout_session(session_id: str) -> stripe.checkout.Session:
    return await _client.v1.checkout.sessions.retrieve_async(session_id)


async def close_checkout_session(session_id: str) -> str:
    """Makes the payment page impossible to pay on.

    Returns how the session ended: "expired" (not paid, and now it can't
    be) or "complete" (it was paid before we could close it).
    """
    try:
        session = await _client.v1.checkout.sessions.expire_async(session_id)
    except stripe.InvalidRequestError as exc:
        # Stripe refuses when the session is not open any more (400, checked
        # on the sandbox): it expired by itself or has just been paid.
        # Ask which.
        session = await get_checkout_session(session_id)
        if session.status == "open":
            # Stripe refused to close an open page: the order must not be
            # canceled while it can still be paid.
            raise exc
    return session.status


def parse_webhook_event(payload: bytes, signature: str) -> stripe.Event:
    """Reads an event Stripe sent to our webhook.

    The signature proves that Stripe sent exactly this body, and recently
    (5 minutes), so an old request can't be replayed. Raises
    InvalidWebhookError otherwise. No request to Stripe is made.
    """
    try:
        return _client.construct_event(
            payload, signature, settings.stripe_webhook_secret
        )
    # ValueError: the body is not JSON / not UTF-8.
    except (stripe.SignatureVerificationError, ValueError) as exc:
        raise InvalidWebhookError(str(exc)) from exc
