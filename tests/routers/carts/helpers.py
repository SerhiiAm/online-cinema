"""Shared helpers for the cart tests."""

from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.carts import Cart, CartItem
from tests.routers.movies.helpers import auth


async def add_to_cart(client: AsyncClient, token: str, movie_id: int) -> Response:
    return await client.post(
        "/cart/items", json={"movie_id": movie_id}, headers=auth(token)
    )


async def get_cart(client: AsyncClient, token: str) -> Response:
    return await client.get("/cart", headers=auth(token))


async def remove_from_cart(
    client: AsyncClient, token: str, movie_id: int
) -> Response:
    return await client.delete(f"/cart/items/{movie_id}", headers=auth(token))


async def clear_cart(client: AsyncClient, token: str) -> Response:
    return await client.delete("/cart", headers=auth(token))


async def get_user_cart(client: AsyncClient, token: str, user_id: int) -> Response:
    return await client.get(f"/cart/users/{user_id}", headers=auth(token))


def cart_names(cart: dict) -> list[str]:
    """Movie names in a cart response, in the order shown."""
    return [item["movie"]["name"] for item in cart["items"]]


async def count_cart_rows(db: AsyncSession) -> dict[str, int]:
    """Rows in carts and cart_items."""
    await db.rollback()  # see what other sessions committed
    return {
        "carts": await db.scalar(select(func.count()).select_from(Cart)),
        "items": await db.scalar(select(func.count()).select_from(CartItem)),
    }
