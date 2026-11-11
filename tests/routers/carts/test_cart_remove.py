import asyncio

from httpx import AsyncClient

from app.core.dependencies import MAX_DB_ID
from tests.routers.carts.helpers import (
    add_to_cart,
    cart_names,
    clear_cart,
    get_cart,
    remove_from_cart,
)

EMPTY_CART = {"items": [], "total": "0"}


async def fill_cart(
    client: AsyncClient, token: str, movies: dict[str, int], *names: str
) -> None:
    for name in names:
        response = await add_to_cart(client, token, movies[name])
        assert response.status_code == 201


async def test_remove_one_movie(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await fill_cart(client, user_token, movies, "Inception", "Memento", "Tenet")

    response = await remove_from_cart(client, user_token, movies["Memento"])

    assert response.status_code == 204
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Inception", "Tenet"]
    assert cart["total"] == "27.49"  # 19.99 + 7.50


async def test_remove_movie_not_in_cart_is_404(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await fill_cart(client, user_token, movies, "Inception")

    response = await remove_from_cart(client, user_token, movies["Tenet"])

    assert response.status_code == 404
    assert response.json() == {"detail": "This movie is not in your cart."}
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Inception"]


async def test_remove_without_cart_is_404(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    response = await remove_from_cart(client, user_token, movies["Tenet"])

    assert response.status_code == 404


async def test_cannot_remove_from_other_users_cart(
    client: AsyncClient,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await fill_cart(client, user_token, movies, "Inception")
    # The other user has a cart too, just without this movie.
    await fill_cart(client, other_token, movies, "Tenet")

    response = await remove_from_cart(client, other_token, movies["Inception"])

    assert response.status_code == 404
    mine = (await get_cart(client, user_token)).json()
    assert cart_names(mine) == ["Inception"]


async def test_removed_movie_can_be_added_again(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await fill_cart(client, user_token, movies, "Inception")
    await remove_from_cart(client, user_token, movies["Inception"])

    response = await add_to_cart(client, user_token, movies["Inception"])

    assert response.status_code == 201
    assert cart_names(response.json()) == ["Inception"]


async def test_remove_movie_id_above_db_integer_is_422(
    client: AsyncClient, user_token: str
) -> None:
    response = await remove_from_cart(client, user_token, MAX_DB_ID + 1)

    assert response.status_code == 422


async def test_clear_cart(
    client: AsyncClient,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await fill_cart(client, user_token, movies, "Inception", "Memento")
    await fill_cart(client, other_token, movies, "Inception")

    response = await clear_cart(client, user_token)

    assert response.status_code == 204
    assert (await get_cart(client, user_token)).json() == EMPTY_CART
    theirs = (await get_cart(client, other_token)).json()
    assert cart_names(theirs) == ["Inception"]


async def test_clear_without_cart_or_empty_is_204(
    client: AsyncClient, user_token: str
) -> None:
    # No cart yet, then clearing again: neither is an error.
    assert (await clear_cart(client, user_token)).status_code == 204
    assert (await clear_cart(client, user_token)).status_code == 204
    assert (await get_cart(client, user_token)).json() == EMPTY_CART


async def test_parallel_remove_of_same_movie(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await fill_cart(client, user_token, movies, "Inception")

    responses = await asyncio.gather(
        remove_from_cart(client, user_token, movies["Inception"]),
        remove_from_cart(client, user_token, movies["Inception"]),
    )

    assert sorted(r.status_code for r in responses) == [204, 404]
