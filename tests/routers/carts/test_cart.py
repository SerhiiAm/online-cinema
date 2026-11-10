import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID
from tests.routers.carts.helpers import (
    add_to_cart,
    cart_names,
    count_cart_rows,
    get_cart,
)

EMPTY_CART = {"items": [], "total": "0"}


@pytest.mark.parametrize(
    "method, path",
    [("GET", "/cart"), ("POST", "/cart/items")],
)
async def test_cart_needs_login(client: AsyncClient, method: str, path: str) -> None:
    response = await client.request(method, path, json={"movie_id": 1})

    assert response.status_code == 401


async def test_new_user_has_empty_cart(client: AsyncClient, user_token: str) -> None:
    response = await get_cart(client, user_token)

    assert response.status_code == 200
    assert response.json() == EMPTY_CART


async def test_add_returns_whole_cart(
    client: AsyncClient, user_token: str, movies: dict[str, int], refs: dict[str, int]
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])

    response = await add_to_cart(client, user_token, movies["Memento"])

    assert response.status_code == 201
    cart = response.json()
    assert cart_names(cart) == ["Inception", "Memento"]
    first = cart["items"][0]
    assert first["movie"] == {
        "id": movies["Inception"],
        "name": "Inception",
        "year": 2010,
        "price": "19.99",
        "genres": [
            {"id": refs["action"], "name": "Action"},
            {"id": refs["drama"], "name": "Drama"},
        ],
    }
    assert first["added_at"]


async def test_get_shows_movies_in_order_added(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    for name in ("Tenet", "Inception", "Memento"):
        await add_to_cart(client, user_token, movies[name])

    response = await get_cart(client, user_token)

    assert response.status_code == 200
    assert cart_names(response.json()) == ["Tenet", "Inception", "Memento"]


async def test_total_is_exact_decimal_sum(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    # 19.99 + 5.01 as floats is 24.999999999999996.
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Memento"])

    response = await get_cart(client, user_token)

    assert response.json()["total"] == "25.00"


async def test_same_movie_twice_is_409(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])

    response = await add_to_cart(client, user_token, movies["Inception"])

    assert response.status_code == 409
    assert response.json() == {"detail": "This movie is already in your cart."}
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Inception"]


async def test_unknown_movie_is_404_and_saves_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    response = await add_to_cart(client, user_token, MAX_DB_ID)

    assert response.status_code == 404
    assert response.json() == {"detail": "Movie not found."}
    # Not even an empty cart is left behind.
    assert await count_cart_rows(db_session) == {"carts": 0, "items": 0}
    # A failed add doesn't block the next one.
    response = await add_to_cart(client, user_token, movies["Tenet"])
    assert response.status_code == 201


async def test_movie_id_above_db_integer_is_422(
    client: AsyncClient, user_token: str
) -> None:
    response = await add_to_cart(client, user_token, MAX_DB_ID + 1)

    assert response.status_code == 422


async def test_each_user_sees_only_own_cart(
    client: AsyncClient,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])

    # The same movie in another cart is not a duplicate.
    response = await add_to_cart(client, other_token, movies["Inception"])
    assert response.status_code == 201
    await add_to_cart(client, other_token, movies["Tenet"])

    mine = (await get_cart(client, user_token)).json()
    theirs = (await get_cart(client, other_token)).json()
    assert cart_names(mine) == ["Inception"]
    assert cart_names(theirs) == ["Inception", "Tenet"]


async def test_parallel_first_adds_create_one_cart(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    responses = await asyncio.gather(
        add_to_cart(client, user_token, movies["Inception"]),
        add_to_cart(client, user_token, movies["Memento"]),
    )

    assert [r.status_code for r in responses] == [201, 201]
    assert await count_cart_rows(db_session) == {"carts": 1, "items": 2}


async def test_parallel_same_movie_is_added_once(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    responses = await asyncio.gather(
        add_to_cart(client, user_token, movies["Inception"]),
        add_to_cart(client, user_token, movies["Inception"]),
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert await count_cart_rows(db_session) == {"carts": 1, "items": 1}
