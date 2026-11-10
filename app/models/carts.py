from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.movies import Movie


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique: one cart per user. Deleting the user deletes the cart.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )


class CartItem(Base):
    __tablename__ = "cart_items"
    # The same movie can't be in one cart twice.
    __table_args__ = (
        UniqueConstraint("cart_id", "movie_id", name="uq_cart_items_cart_movie"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"))
    # CASCADE: a deleted movie disappears from every cart.
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE")
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    movie: Mapped[Movie] = relationship()
