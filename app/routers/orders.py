from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import AdminUser, CurrentUser, PathId
from app.crud import orders as crud
from app.models.orders import OrderStatusEnum
from app.routers.carts import AUTH_ERRORS
from app.schemas.orders import (
    AdminOrderResponse,
    OrderCreatedResponse,
    OrderListQuery,
    OrderMovie,
    OrderResponse,
)
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
ORDER_NOT_FOUND = "Order not found."
NOT_CANCELABLE = {
    OrderStatusEnum.PAID: "A paid order can't be canceled.",
    OrderStatusEnum.CANCELED: "The order is already canceled.",
}


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


@router.get(
    "/all",
    summary="List the orders of all users (admin)",
    description=(
        "Every order with its `user_id`, newest first, "
        f"{PAGE_SIZE} per page. Filters are optional and can be combined: "
        "`user_id`, `status` (`pending`, `paid` or `canceled`) and the days "
        "`date_from` / `date_to` (`YYYY-MM-DD`, both included). Days are "
        "counted in UTC, like the times in the answer. A filter that matches "
        "nothing (an unknown user, `date_from` after `date_to`) gives an "
        "empty page."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTH_ERRORS[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_403_FORBIDDEN: {
            "description": "You are not an admin, or the account is inactive."
        },
    },
)
async def list_all_orders(
    query: Annotated[OrderListQuery, Query()], admin: AdminUser, db: DbSession
) -> Page[AdminOrderResponse]:
    orders, total = await crud.list_all_orders(db, query)
    return Page[AdminOrderResponse].model_validate(
        {"items": orders, "total": total, "page": query.page}, from_attributes=True
    )


@router.post(
    "/{order_id}/cancel",
    summary="Cancel your unpaid order",
    description=(
        "Changes the status of your `pending` order to `canceled` and "
        "returns the order. It stays in your list of orders, and your cart "
        "is not changed, so you can create a new order from it. "
        "A paid order can't be canceled."
    ),
    responses={
        **AUTH_ERRORS,
        status.HTTP_404_NOT_FOUND: {"description": "You have no order with this id."},
        status.HTTP_409_CONFLICT: {
            "description": "The order is paid or already canceled. "
            "The message says which."
        },
    },
)
async def cancel_order(
    order_id: PathId, user: CurrentUser, db: DbSession
) -> OrderResponse:
    try:
        await crud.cancel_order(db, order_id, user.id)
    except crud.OrderNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ORDER_NOT_FOUND
        )
    except crud.OrderNotPendingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=NOT_CANCELABLE[exc.status]
        )
    order = await crud.get_order(db, order_id)
    return OrderResponse.model_validate(order)
