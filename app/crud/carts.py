from sqlalchemy import ScalarSelect, delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.carts import Cart, CartItem
from app.models.movies import Movie
from app.models.orders import Order, OrderItem, OrderStatusEnum


class AlreadyPurchasedError(Exception):
    """The user has already bought this movie."""


def _cart_id_of(user_id: int) -> ScalarSelect[int]:
    """The user's cart id as a subquery (NULL if no cart, then nothing matches)."""
    return select(Cart.id).where(Cart.user_id == user_id).scalar_subquery()


async def _get_or_create_cart_id(db: AsyncSession, user_id: int) -> int:
    # ON CONFLICT DO NOTHING: two parallel first adds don't fail on the
    # unique user_id; the second one waits and then just reads the id.
    await db.execute(
        pg_insert(Cart)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=[Cart.user_id])
    )
    return await db.scalar(select(Cart.id).where(Cart.user_id == user_id))


async def add_to_cart(db: AsyncSession, user_id: int, movie_id: int) -> None:
    """Raises AlreadyPurchasedError if the movie is in the user's paid order.

    The rest is not checked beforehand: the DB decides (unique cart + movie,
    existing movie), so a parallel request can't slip past a check.
    A violation raises IntegrityError and nothing is saved, not even a new
    empty cart.
    """
    # Checked first, so a movie that is both bought and in the cart gets the
    # more useful answer. No lock: if the order is paid right after this
    # check, "create order" still leaves the bought movie out.
    purchased = await db.scalar(
        select(OrderItem.id)
        .join(Order)
        .where(
            Order.user_id == user_id,
            Order.status == OrderStatusEnum.PAID,
            OrderItem.movie_id == movie_id,
        )
        .limit(1)
    )
    if purchased is not None:
        raise AlreadyPurchasedError
    cart_id = await _get_or_create_cart_id(db, user_id)
    await db.execute(insert(CartItem).values(cart_id=cart_id, movie_id=movie_id))
    await db.commit()


async def remove_from_cart(db: AsyncSession, user_id: int, movie_id: int) -> bool:
    """Returns False if the movie is not in the user's cart.

    One DELETE: the check and the removal can't be split by a parallel
    request, and the user filter keeps other carts out of reach.
    """
    removed_id = await db.scalar(
        delete(CartItem)
        .where(CartItem.cart_id == _cart_id_of(user_id), CartItem.movie_id == movie_id)
        .returning(CartItem.id)
    )
    await db.commit()
    return removed_id is not None


async def clear_cart(db: AsyncSession, user_id: int) -> None:
    """Removes all movies; the (empty) cart itself stays."""
    await db.execute(delete(CartItem).where(CartItem.cart_id == _cart_id_of(user_id)))
    await db.commit()


async def get_cart_items(db: AsyncSession, user_id: int) -> list[CartItem]:
    """The user's cart in the order the movies were added; [] if no cart yet."""
    items = await db.scalars(
        select(CartItem)
        .join(Cart)
        .where(Cart.user_id == user_id)
        .order_by(CartItem.added_at, CartItem.id)
        # Loaded right away: lazy loading in async code fails -> 500.
        .options(joinedload(CartItem.movie).selectinload(Movie.genres))
    )
    return list(items)
