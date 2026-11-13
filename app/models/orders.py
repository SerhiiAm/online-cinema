import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.movies import Movie


class OrderStatusEnum(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"


class OrderItem(Base):
    __tablename__ = "order_items"
    # The same movie can't be in one order twice.
    __table_args__ = (
        UniqueConstraint("order_id", "movie_id", name="uq_order_items_order_movie"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    # RESTRICT: a movie that is in any order can't be deleted, so an order
    # never loses a movie it was created (and maybe paid) with.
    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="RESTRICT")
    )
    # The price when the order was created: later price changes don't
    # rewrite the order.
    price_at_order: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    movie: Mapped[Movie] = relationship()


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # No CASCADE: orders are financial history and must not disappear
    # together with the user.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[OrderStatusEnum] = mapped_column(
        Enum(OrderStatusEnum),
        default=OrderStatusEnum.PENDING,
        server_default=OrderStatusEnum.PENDING.name,
    )
    # The sum of price_at_order of the items.
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    items: Mapped[list[OrderItem]] = relationship(order_by=OrderItem.id)
