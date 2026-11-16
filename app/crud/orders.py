from collections.abc import Sequence
from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy import ColumnElement, Row, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.payment_gateway import MAX_PAYMENT, MIN_PAYMENT
from app.crud.pagination import page_of
from app.models.carts import Cart, CartItem
from app.models.movies import Movie
from app.models.orders import Order, OrderItem, OrderStatusEnum
from app.schemas.orders import OrderListQuery

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


class OrderNotFoundError(Exception):
    """The user has no order with this id."""


class OrderNotPendingError(Exception):
    """The order is paid or already canceled, so it can't be canceled."""

    def __init__(self, status: OrderStatusEnum) -> None:
        super().__init__(f"The order is {status.value}.")
        self.status = status


# Loaded right away: lazy loading in async code fails -> 500.
ORDER_DETAILS = selectinload(Order.items).joinedload(OrderItem.movie)


async def _lock_pending_order(
    db: AsyncSession, order_id: int, user_id: int
) -> Order:
    """Locks the user's order until the transaction ends; it must be pending.
    Returns it with its items and their movies."""
    order = await db.scalar(
        select(Order)
        .where(Order.id == order_id, Order.user_id == user_id)
        .options(ORDER_DETAILS)
        .with_for_update(of=Order)
    )
    if order is None:
        raise OrderNotFoundError
    if order.status != OrderStatusEnum.PENDING:
        raise OrderNotPendingError(order.status)
    return order


async def lock_order_for_cancel(
    db: AsyncSession, order_id: int, user_id: int
) -> Order:
    """Locks the user's pending order until the transaction ends.

    While we close its Stripe page, a payment (it locks the order too) or
    a new page ("checkout" locks it as well) has to wait, and they see the
    order canceled afterwards. Raises OrderNotFoundError or
    OrderNotPendingError; the lock is released then.
    """
    try:
        return await _lock_pending_order(db, order_id, user_id)
    except Exception:
        await db.rollback()  # release the lock, nothing was changed
        raise


async def mark_canceled(db: AsyncSession, order: Order) -> None:
    """Cancels the locked order and ends the transaction (so the lock is
    released)."""
    order.status = OrderStatusEnum.CANCELED
    await db.commit()


class PricesChangedError(Exception):
    """A movie of the order costs something else now."""


class AmountTooSmallError(Exception):
    pass


class AmountTooLargeError(Exception):
    pass


async def lock_order_for_payment(
    db: AsyncSession, order_id: int, user_id: int
) -> Order:
    """Locks the user's order until the transaction ends and checks that it
    can be paid now. Returns it with its items and their movies.

    Raises OrderNotFoundError, OrderNotPendingError, PricesChangedError,
    AmountTooSmallError or AmountTooLargeError; the lock is released then.
    """
    try:
        return await _lock_order_for_payment(db, order_id, user_id)
    except Exception:
        await db.rollback()  # release the lock, nothing was changed
        raise


async def _lock_order_for_payment(
    db: AsyncSession, order_id: int, user_id: int
) -> Order:
    # The lock makes a second "pay" of the same order (a double click) wait
    # until the first one has saved its Stripe session, so it reuses that
    # session instead of opening a second page where the order could be
    # paid twice.
    order = await _lock_pending_order(db, order_id, user_id)
    # The user pays the prices of the order; if the catalog says otherwise,
    # they must see the new prices in a new order first.
    if any(item.movie.price != item.price_at_order for item in order.items):
        raise PricesChangedError
    if order.total_amount < MIN_PAYMENT:
        raise AmountTooSmallError
    if order.total_amount > MAX_PAYMENT:
        raise AmountTooLargeError
    return order


async def save_checkout_session(
    db: AsyncSession, order_id: int, session_id: str
) -> None:
    """Remembers the order's Stripe session and ends the transaction (so
    the order's lock is released)."""
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(checkout_session_id=session_id)
    )
    await db.commit()


async def get_order(db: AsyncSession, order_id: int) -> Order | None:
    return await db.scalar(
        select(Order).where(Order.id == order_id).options(ORDER_DETAILS)
    )


async def _orders_page(
    db: AsyncSession, conditions: list[ColumnElement[bool]], page: int
) -> tuple[list[Order], int]:
    """One page of the matching orders, newest first, and how many of them
    there are on all pages."""
    total = await db.scalar(select(func.count()).select_from(Order).where(*conditions))
    orders = await db.scalars(
        page_of(
            select(Order)
            .where(*conditions)
            # Ids grow in the order of creation, and are never equal.
            .order_by(Order.id.desc())
            .options(ORDER_DETAILS),
            page,
        )
    )
    return list(orders), total


async def list_user_orders(
    db: AsyncSession, user_id: int, page: int
) -> tuple[list[Order], int]:
    return await _orders_page(db, [Order.user_id == user_id], page)


async def list_all_orders(
    db: AsyncSession, query: OrderListQuery
) -> tuple[list[Order], int]:
    """The orders of all users that match the filters (for admins)."""
    conditions = []
    if query.user_id is not None:
        conditions.append(Order.user_id == query.user_id)
    if query.status is not None:
        conditions.append(Order.status == query.status)
    # Whole days in UTC, both included: from 00:00:00 of the first day to
    # the last microsecond of the last one (the DB stores microseconds too).
    if query.date_from is not None:
        start = datetime.combine(query.date_from, time.min, UTC)
        conditions.append(Order.created_at >= start)
    if query.date_to is not None:
        end = datetime.combine(query.date_to, time.max, UTC)
        conditions.append(Order.created_at <= end)
    return await _orders_page(db, conditions, query.page)
