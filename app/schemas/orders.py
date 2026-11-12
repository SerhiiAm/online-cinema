from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.orders import OrderStatusEnum


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
