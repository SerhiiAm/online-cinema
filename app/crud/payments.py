from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.orders import ORDER_DETAILS, OrderNotFoundError, OrderNotPendingError
from app.models.accounts import User
from app.models.carts import Cart, CartItem
from app.models.orders import Order, OrderStatusEnum
from app.models.payments import Payment, PaymentItem


class PaymentAmountMismatchError(Exception):
    """Stripe took another amount than the order's total."""


@dataclass(frozen=True)
class PaidOrder:
    """What the confirmation email needs about a just paid order."""

    order_id: int
    email: str
    amount: Decimal
    movie_names: list[str]


async def pay_order(
    db: AsyncSession, order_id: int, amount: Decimal, external_payment_id: str | None
) -> PaidOrder | None:
    """Marks the pending order paid after Stripe has taken `amount`.

    In one transaction: the order becomes paid, the payment is saved with
    its items, and the order's movies leave the user's cart.
    Returns None if the order is already paid (Stripe sent the same payment
    again). Raises OrderNotFoundError, OrderNotPendingError (the order is
    canceled) or PaymentAmountMismatchError; nothing is changed then.
    """
    try:
        return await _pay_order(db, order_id, amount, external_payment_id)
    except Exception:
        await db.rollback()  # release the locks, nothing was changed
        raise


async def _pay_order(
    db: AsyncSession, order_id: int, amount: Decimal, external_payment_id: str | None
) -> PaidOrder | None:
    user_id = await db.scalar(select(Order.user_id).where(Order.id == order_id))
    if user_id is None:
        raise OrderNotFoundError
    # The same lock as "create order" takes: while it reads which movies
    # are bought or in unpaid orders, this order can't turn paid under it.
    # Always the cart first, then the order: the same order of locks
    # everywhere, so two requests never wait for each other forever.
    cart_id = await db.scalar(
        select(Cart.id).where(Cart.user_id == user_id).with_for_update()
    )
    # Two copies of the same event (Stripe may send one twice, even at the
    # same moment) wait here for each other; the second sees "paid".
    order = await db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(ORDER_DETAILS)
        .with_for_update(of=Order)
    )
    if order.status == OrderStatusEnum.PAID:
        return None
    if order.status != OrderStatusEnum.PENDING:
        raise OrderNotPendingError(order.status)
    if amount != order.total_amount:
        raise PaymentAmountMismatchError

    await db.execute(
        update(Order).where(Order.id == order_id).values(status=OrderStatusEnum.PAID)
    )
    payment_id = await db.scalar(
        insert(Payment)
        .values(
            user_id=user_id,
            order_id=order_id,
            amount=order.total_amount,
            external_payment_id=external_payment_id,
        )
        .returning(Payment.id)
    )
    await db.execute(
        insert(PaymentItem),
        [
            {
                "payment_id": payment_id,
                "order_item_id": item.id,
                "price_at_payment": item.price_at_order,
            }
            for item in order.items
        ],
    )
    # Only the movies of this order: the rest of the cart stays.
    await db.execute(
        delete(CartItem).where(
            CartItem.cart_id == cart_id,
            CartItem.movie_id.in_([item.movie_id for item in order.items]),
        )
    )
    email = await db.scalar(select(User.email).where(User.id == user_id))
    paid = PaidOrder(
        order_id=order_id,
        email=email,
        amount=order.total_amount,
        movie_names=[item.movie.name for item in order.items],
    )
    await db.commit()
    return paid
