import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaymentStatusEnum(str, enum.Enum):
    # Only SUCCESSFUL is set for now: a row is written when a payment
    # succeeds, and refunds are out of scope. The other two are kept so the
    # table matches the specification.
    SUCCESSFUL = "successful"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    # No CASCADE (here and on order_id): payments are financial history.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[PaymentStatusEnum] = mapped_column(
        Enum(PaymentStatusEnum),
        default=PaymentStatusEnum.SUCCESSFUL,
        server_default=PaymentStatusEnum.SUCCESSFUL.name,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # The Stripe payment id. Unique: one Stripe payment can't be recorded
    # twice, even if its webhook comes again.
    external_payment_id: Mapped[str | None] = mapped_column(
        String(255), unique=True
    )


class PaymentItem(Base):
    __tablename__ = "payment_items"
    # One order line can't be in one payment twice.
    __table_args__ = (
        UniqueConstraint(
            "payment_id", "order_item_id", name="uq_payment_items_payment_order_item"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE")
    )
    # RESTRICT: a paid order line can't disappear from under its payment.
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="RESTRICT")
    )
    price_at_payment: Mapped[Decimal] = mapped_column(Numeric(10, 2))
