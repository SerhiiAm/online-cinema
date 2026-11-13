from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.orders import OrderStatusEnum
from app.schemas.movies import DbId
from app.schemas.pagination import PageNumber


class OrderMovie(BaseModel):
    """What an order shows about a movie."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    year: int


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    movie: OrderMovie
    price_at_order: Decimal


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    status: OrderStatusEnum
    total_amount: Decimal
    items: list[OrderItemResponse]


class OrderCreatedResponse(OrderResponse):
    # Movies from the cart that were left out of the order because the user
    # has already bought them.
    excluded: list[OrderMovie]


# --- All orders (admin) ---


class AdminOrderResponse(OrderResponse):
    # An admin sees the orders of everyone, so each one says whose it is.
    user_id: int


class OrderListQuery(BaseModel):
    """Filters are optional and can be combined."""

    page: PageNumber = 1
    user_id: DbId | None = None
    status: OrderStatusEnum | None = None
    date_from: date | None = Field(
        default=None, description="Created on this day (UTC) or later."
    )
    date_to: date | None = Field(
        default=None, description="Created on this day (UTC) or earlier."
    )
