from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.carts import Cart, CartItem
from app.models.movies import Movie


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
    """Nothing is checked beforehand: the DB decides (unique cart + movie,
    existing movie), so a parallel request can't slip past a check.
    A violation raises IntegrityError and nothing is saved, not even a new
    empty cart.
    """
    # Orders (feature 6): a movie the user has already bought will be
    # refused here.
    cart_id = await _get_or_create_cart_id(db, user_id)
    await db.execute(insert(CartItem).values(cart_id=cart_id, movie_id=movie_id))
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
