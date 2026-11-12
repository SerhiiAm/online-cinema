from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Row, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.carts import Cart, CartItem
from app.models.movies import Movie
from app.models.orders import Order, OrderItem, OrderStatusEnum

# The largest value of the DECIMAL(10, 2) column orders.total_amount.
MAX_TOTAL = Decimal("99999999.99")


class OrderRefusedError(Exception):
    """The order can't be created from the cart as it is now."""


class EmptyCartError(OrderRefusedError):
    pass


class AllPurchasedError(OrderRefusedError):
    """Every movie in the cart has already been bought by the user."""


class PendingOrdersError(OrderRefusedError):
    """Some movies are already in the user's unpaid orders."""

    def __init__(self, order_ids: list[int]) -> None:
        super().__init__(f"Unpaid orders with the same movies: {order_ids}.")
        self.order_ids = order_ids


class TotalTooLargeError(OrderRefusedError):
    pass


async def create_order(db: AsyncSession, user_id: int) -> tuple[int, Sequence[Row]]:
    """Creates a pending order from the user's cart; the cart stays as it is.

    Returns the order id and the movies (id, name, year) left out because
    the user has already bought them. Raises an OrderRefusedError subclass
    if the order can't be created; nothing is saved then.
    """
    try:
        return await _create_order(db, user_id)
    except OrderRefusedError:
        await db.rollback()  # release the locks, nothing was changed
        raise


async def _create_order(db: AsyncSession, user_id: int) -> tuple[int, Sequence[Row]]:
    # The cart row is locked: a second "create order" of the same user
    # (a double click) waits here and then sees the order made by the first.
    cart_id = await db.scalar(
        select(Cart.id).where(Cart.user_id == user_id).with_for_update()
    )
    # The movies are locked too (shared: other orders may read them, but a
    # price change or a delete waits), so the prices can't change and no
    # movie can disappear until the order is saved.
    # A user without a cart has cart_id None: no rows, like an empty cart.
    movies = (
        await db.execute(
            select(Movie.id, Movie.name, Movie.year, Movie.price)
            .join(CartItem, CartItem.movie_id == Movie.id)
            .where(CartItem.cart_id == cart_id)
            .order_by(CartItem.added_at, CartItem.id)
            .with_for_update(read=True, of=Movie)
        )
    ).all()
    if not movies:
        raise EmptyCartError

    # The user's paid and pending orders that contain movies of the cart.
    in_orders = (
        await db.execute(
            select(OrderItem.movie_id, Order.id, Order.status)
            .join(Order)
            .where(
                Order.user_id == user_id,
                Order.status != OrderStatusEnum.CANCELED,
                OrderItem.movie_id.in_(
                    select(CartItem.movie_id).where(CartItem.cart_id == cart_id)
                ),
            )
        )
    ).all()
    purchased = {
        movie_id
        for movie_id, _, status in in_orders
        if status == OrderStatusEnum.PAID
    }
    excluded = [movie for movie in movies if movie.id in purchased]
    to_order = [movie for movie in movies if movie.id not in purchased]
    if not to_order:
        raise AllPurchasedError
    pending = {
        order_id
        for movie_id, order_id, status in in_orders
        if status == OrderStatusEnum.PENDING and movie_id not in purchased
    }
    if pending:
        raise PendingOrdersError(sorted(pending))

    # Decimal, not float: 0.1 + 0.2 must be exactly 0.3.
    total = sum((movie.price for movie in to_order), Decimal(0))
    if total > MAX_TOTAL:
        raise TotalTooLargeError

    order_id = await db.scalar(
        insert(Order).values(user_id=user_id, total_amount=total).returning(Order.id)
    )
    await db.execute(
        insert(OrderItem),
        [
            {"order_id": order_id, "movie_id": movie.id, "price_at_order": movie.price}
            for movie in to_order
        ],
    )
    await db.commit()
    return order_id, excluded


async def get_order(db: AsyncSession, order_id: int) -> Order | None:
    return await db.scalar(
        select(Order)
        .where(Order.id == order_id)
        # Loaded right away: lazy loading in async code fails -> 500.
        .options(selectinload(Order.items).joinedload(OrderItem.movie))
    )
