from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import AdminUser, CurrentUser, PathId
from app.crud import carts as crud
from app.crud.accounts import get_user_by_id
from app.routers.accounts import USER_NOT_FOUND
from app.routers.movies import FOREIGN_KEY_VIOLATION, UNIQUE_VIOLATION
from app.schemas.carts import CartItemAdd, CartItemResponse, CartResponse

router = APIRouter(prefix="/cart", tags=["Cart"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

ALREADY_IN_CART = "This movie is already in your cart."
ALREADY_PURCHASED = "You have already bought this movie."
MOVIE_NOT_FOUND = "Movie not found."
NOT_IN_CART = "This movie is not in your cart."

AUTH_ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {
        "description": "Log in first. No account yet? Sign up with "
        "`POST /accounts/register`."
    },
    status.HTTP_403_FORBIDDEN: {"description": "The account is inactive."},
}


async def _cart_response(db: AsyncSession, user_id: int) -> CartResponse:
    items = await crud.get_cart_items(db, user_id)
    return CartResponse(
        items=[CartItemResponse.model_validate(item) for item in items],
        # Decimal, not float: 0.1 + 0.2 must be exactly 0.3.
        total=sum((item.movie.price for item in items), Decimal(0)),
    )


@router.get(
    "",
    summary="View your cart",
    description=(
        "The movies in your cart in the order you added them, each with its "
        "title, price, genres and year, and `total` = the sum of the prices. "
        "An empty cart has no items and a total of 0."
    ),
    responses=AUTH_ERRORS,
)
async def get_cart(user: CurrentUser, db: DbSession) -> CartResponse:
    return await _cart_response(db, user.id)


@router.post(
    "/items",
    status_code=status.HTTP_201_CREATED,
    summary="Add a movie to your cart",
    description=(
        "Adds the movie by its id and returns the whole cart. "
        "A movie can be in the cart only once, and a movie you have already "
        "bought can't be added."
    ),
    responses={
        **AUTH_ERRORS,
        status.HTTP_404_NOT_FOUND: {"description": MOVIE_NOT_FOUND},
        status.HTTP_409_CONFLICT: {
            "description": "The movie is already in your cart, or you have "
            "already bought it. The message says which."
        },
    },
)
async def add_to_cart(
    data: CartItemAdd, user: CurrentUser, db: DbSession
) -> CartResponse:
    try:
        await crud.add_to_cart(db, user.id, data.movie_id)
    except crud.AlreadyPurchasedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=ALREADY_PURCHASED
        )
    except IntegrityError as exc:
        # No rollback needed: no more queries here, and the session is
        # closed (rolled back) at the end of the request.
        if exc.orig.sqlstate == UNIQUE_VIOLATION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=ALREADY_IN_CART
            )
        if exc.orig.sqlstate == FOREIGN_KEY_VIOLATION:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=MOVIE_NOT_FOUND
            )
        raise
    return await _cart_response(db, user.id)


@router.delete(
    "/items/{movie_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a movie from your cart",
    description="Removes one movie by its id; the other movies stay.",
    responses={
        **AUTH_ERRORS,
        status.HTTP_404_NOT_FOUND: {"description": NOT_IN_CART},
    },
)
async def remove_from_cart(movie_id: PathId, user: CurrentUser, db: DbSession) -> None:
    if not await crud.remove_from_cart(db, user.id, movie_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_IN_CART)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear your cart",
    description=(
        "Removes all movies from your cart. Clearing an empty cart is not "
        "an error."
    ),
    responses=AUTH_ERRORS,
)
async def clear_cart(user: CurrentUser, db: DbSession) -> None:
    await crud.clear_cart(db, user.id)


@router.get(
    "/users/{user_id}",
    summary="View a user's cart (admin)",
    description=(
        "The same view as `GET /cart`, for any user, for analysis or "
        "troubleshooting. A user without a cart has an empty one; "
        "a user id that doesn't exist is a 404."
    ),
    responses={
        status.HTTP_401_UNAUTHORIZED: AUTH_ERRORS[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_403_FORBIDDEN: {
            "description": "You are not an admin, or the account is inactive."
        },
        status.HTTP_404_NOT_FOUND: {"description": USER_NOT_FOUND},
    },
)
async def get_user_cart(
    user_id: PathId, admin: AdminUser, db: DbSession
) -> CartResponse:
    # Checked so the admin can tell a wrong id from an empty cart.
    if await get_user_by_id(db, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND
        )
    return await _cart_response(db, user_id)
