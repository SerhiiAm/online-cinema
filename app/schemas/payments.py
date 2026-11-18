from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.orders import OrderStatusEnum
from app.models.payments import PaymentStatusEnum
from app.schemas.movies import DbId
from app.schemas.pagination import PageNumber

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


# --- Payment history ---


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    amount: Decimal
    status: PaymentStatusEnum
    order_id: int


class AdminPaymentResponse(PaymentResponse):
    # An admin sees the payments of everyone, so each one says whose it is.
    user_id: int


class PaymentListQuery(BaseModel):
    """Filters are optional and can be combined."""

    page: PageNumber = 1
    user_id: DbId | None = None
    status: PaymentStatusEnum | None = None
    date_from: date | None = Field(
        default=None, description="Made on this day (UTC) or later."
    )
    date_to: date | None = Field(
        default=None, description="Made on this day (UTC) or earlier."
    )
