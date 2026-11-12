"""Shared helpers for the order tests."""

from decimal import Decimal

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orders import Order, OrderItem, OrderStatusEnum


async def save_order(
    db: AsyncSession,
    user_id: int,
    movie_ids: list[int],
    status: OrderStatusEnum = OrderStatusEnum.PENDING,
) -> int:
    """Saves an order straight into the DB, every movie at 9.99.

    For states the API can't produce yet (e.g. a paid order).
    """
    price = Decimal("9.99")
    order_id = await db.scalar(
        insert(Order)
        .values(user_id=user_id, status=status, total_amount=price * len(movie_ids))
        .returning(Order.id)
    )
    await db.execute(
        insert(OrderItem),
        [
            {"order_id": order_id, "movie_id": movie_id, "price_at_order": price}
            for movie_id in movie_ids
        ],
    )
    await db.commit()
    return order_id


async def count_order_rows(db: AsyncSession) -> dict[str, int]:
    """Rows in orders and order_items."""
    await db.rollback()  # see what other sessions committed
    return {
        "orders": await db.scalar(select(func.count()).select_from(Order)),
        "items": await db.scalar(select(func.count()).select_from(OrderItem)),
    }
