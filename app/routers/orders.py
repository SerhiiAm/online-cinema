from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUser
from app.crud import orders as crud
from app.routers.carts import AUTH_ERRORS
from app.schemas.orders import OrderCreatedResponse, OrderMovie, OrderResponse
from app.schemas.pagination import PAGE_SIZE, Page, PageNumber

router = APIRouter(prefix="/orders", tags=["Orders"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

CART_EMPTY = "Your cart is empty."
ALL_PURCHASED = "You have already bought all the movies in your cart."
IN_PENDING_ORDERS = (
    "Some of these movies are already in your unpaid orders: {orders}. "
    "Pay or cancel them first."
)
TOTAL_TOO_LARGE = "The order total is too large. Remove some movies from your cart."


def _refusal_text(exc: crud.OrderRefusedError) -> str:
    if isinstance(exc, crud.PendingOrdersError):
        return IN_PENDING_ORDERS.format(orders=", ".join(map(str, exc.order_ids)))
    return {
        crud.EmptyCartError: CART_EMPTY,
        crud.AllPurchasedError: ALL_PURCHASED,
        crud.TotalTooLargeError: TOTAL_TOO_LARGE,
    }[type(exc)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create an order from your cart",
    description=(
        "Puts all the movies of your cart into a new order with the status "
        "`pending` and their current prices. There is no request body. "
        "The cart is not emptied: the movies leave it when the order is paid. "
        "Movies you have already bought are left out and listed in "
        "`excluded`. While a movie is in your unpaid order you can't order "
        "it again: pay or cancel that order first."
    ),
    responses={
        **AUTH_ERRORS,
        status.HTTP_409_CONFLICT: {
            "description": "The order can't be created now: the cart is empty, "
            "all its movies are already bought, some of them are in your "
            "unpaid orders, or the total is above 99999999.99. "
            "The message says which."
        },
    },
)
async def create_order(user: CurrentUser, db: DbSession) -> OrderCreatedResponse:
    try:
        order_id, excluded = await crud.create_order(db, user.id)
    except crud.OrderRefusedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_refusal_text(exc)
        )
    order = await crud.get_order(db, order_id)
    return OrderCreatedResponse(
        **OrderResponse.model_validate(order).model_dump(),
        excluded=[OrderMovie.model_validate(movie) for movie in excluded],
    )


@router.get(
    "",
    summary="List your orders",
    description=(
        "Your orders, newest first, each with its date, status (`pending`, "
        "`paid` or `canceled`), total and movies. The prices are those of "
        "the moment the order was created, even if a movie costs something "
        f"else now. {PAGE_SIZE} per page; `total` counts all your orders. "
        "A page after the last one is empty."
    ),
    responses=AUTH_ERRORS,
)
async def list_orders(
    user: CurrentUser, db: DbSession, page: Annotated[PageNumber, Query()] = 1
) -> Page[OrderResponse]:
    orders, total = await crud.list_user_orders(db, user.id, page)
    return Page[OrderResponse].model_validate(
        {"items": orders, "total": total, "page": page}, from_attributes=True
    )
