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


def to_minor_units(amount: Decimal) -> int:
    """9.99 -> 999: Stripe takes amounts in grosze, as integers."""
    return int(amount * 100)


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
