from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.models.orders import OrderStatusEnum

# What Stripe's Checkout Session ids look like (cs_test_a1B2…); Stripe
# refuses ids longer than 66 characters. Anything else is rejected before
# Stripe is asked.
CheckoutSessionId = Annotated[
    str, StringConstraints(pattern=r"^cs_[A-Za-z0-9_]+$", max_length=66)
]


class WebhookResponse(BaseModel):
    received: bool = True


class PaymentResultResponse(BaseModel):
    order_id: int
    status: OrderStatusEnum
    message: str


class PaymentCanceledResponse(BaseModel):
    order_id: int
    message: str
