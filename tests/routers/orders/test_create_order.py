"""POST /orders: an order is created from the cart."""

import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.carts import Cart
from app.models.movies import Movie
from app.models.orders import OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL
from tests.routers.carts.helpers import (
    add_to_cart,
    cart_names,
    clear_cart,
    get_cart,
    remove_from_cart,
)
from tests.routers.movies.helpers import save_movie
from tests.routers.orders.helpers import (
    count_order_rows,
    create_order,
    order_names,
    save_order,
    user_id_of,
)

NO_ORDERS = {"orders": 0, "items": 0}
CART_EMPTY = {"detail": "Your cart is empty."}
ALL_PURCHASED = {"detail": "You have already bought all the movies in your cart."}
TOTAL_TOO_LARGE = {
    "detail": "The order total is too large. Remove some movies from your cart."
}


def in_pending_orders(orders: str) -> dict[str, str]:
    return {
        "detail": f"Some of these movies are already in your unpaid orders: {orders}. "
        "Pay or cancel them first."
    }


async def test_order_is_created_from_the_cart(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # Added in the reverse order of the ids: the order keeps the cart's order.
    await add_to_cart(client, user_token, movies["Tenet"])
    await add_to_cart(client, user_token, movies["Inception"])

    response = await create_order(client, user_token)

    assert response.status_code == 201
    order = response.json()
    assert order.pop("created_at")
    assert order == {
        "id": 1,
        "status": "pending",
        "total_amount": "27.49",
        "items": [
            {
                "movie": {"id": movies["Tenet"], "name": "Tenet", "year": 2010},
                "price_at_order": "7.50",
            },
            {
                "movie": {"id": movies["Inception"], "name": "Inception", "year": 2010},
                "price_at_order": "19.99",
            },
        ],
        "excluded": [],
    }
    assert await count_order_rows(db_session) == {"orders": 1, "items": 2}
    # The cart is emptied only when the order is paid.
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Tenet", "Inception"]


async def test_without_token_is_401(client: AsyncClient) -> None:
    response = await client.post("/orders")

    assert response.status_code == 401


@pytest.mark.parametrize("had_movies", [False, True], ids=["no cart", "cleared cart"])
async def test_empty_cart_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
    had_movies: bool,
) -> None:
    if had_movies:
        await add_to_cart(client, user_token, movies["Inception"])
        await clear_cart(client, user_token)

    response = await create_order(client, user_token)

    assert response.status_code == 409
    assert response.json() == CART_EMPTY
    assert await count_order_rows(db_session) == NO_ORDERS


async def test_only_own_cart_is_ordered(
    client: AsyncClient,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, other_token, movies["Inception"])
    await add_to_cart(client, other_token, movies["Tenet"])

    mine = await create_order(client, user_token)
    # The same movie in another user's unpaid order is not a conflict.
    theirs = await create_order(client, other_token)

    assert mine.status_code == theirs.status_code == 201
    assert order_names(mine.json()) == ["Inception"]
    assert order_names(theirs.json()) == ["Inception", "Tenet"]
    assert theirs.json()["total_amount"] == "27.49"


# --- Movies the user has already bought ---


async def test_purchased_movie_is_excluded(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Memento"])
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Memento"]], OrderStatusEnum.PAID)

    response = await create_order(client, user_token)

    assert response.status_code == 201
    order = response.json()
    assert order_names(order) == ["Inception"]
    assert order["total_amount"] == "19.99"
    assert order["excluded"] == [
        {"id": movies["Memento"], "name": "Memento", "year": 2010}
    ]
    # Nothing is removed from the cart.
    cart = (await get_cart(client, user_token)).json()
    assert cart_names(cart) == ["Inception", "Memento"]


async def test_all_movies_purchased_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Memento"])
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Memento"]], OrderStatusEnum.PAID)

    response = await create_order(client, user_token)

    assert response.status_code == 409
    assert response.json() == ALL_PURCHASED
    assert await count_order_rows(db_session) == {"orders": 1, "items": 1}


async def test_purchases_and_orders_of_others_do_not_count(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Memento"])
    other_id = await user_id_of(db_session, OTHER_EMAIL)
    await save_order(db_session, other_id, [movies["Inception"]], OrderStatusEnum.PAID)
    await save_order(db_session, other_id, [movies["Memento"]])

    response = await create_order(client, user_token)

    assert response.status_code == 201
    assert order_names(response.json()) == ["Inception", "Memento"]
    assert response.json()["excluded"] == []


async def test_purchased_movie_is_excluded_before_the_pending_check(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # A bought movie is not ordered at all, so its old unpaid order is not
    # a reason to refuse the rest of the cart.
    await add_to_cart(client, user_token, movies["Memento"])
    await add_to_cart(client, user_token, movies["Tenet"])
    user_id = await user_id_of(db_session)
    await save_order(db_session, user_id, [movies["Memento"]], OrderStatusEnum.PAID)
    await save_order(db_session, user_id, [movies["Memento"]])

    response = await create_order(client, user_token)

    assert response.status_code == 201
    assert order_names(response.json()) == ["Tenet"]


# --- Movies that are already in the user's unpaid orders ---


async def test_movie_in_unpaid_order_is_409(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Tenet"])
    await create_order(client, user_token)
    # Not the same set of movies any more, but two of them are in order 1.
    await add_to_cart(client, user_token, movies["Memento"])

    response = await create_order(client, user_token)

    assert response.status_code == 409
    assert response.json() == in_pending_orders("1")
    assert await count_order_rows(db_session) == {"orders": 1, "items": 2}


async def test_all_conflicting_unpaid_orders_are_named(
    client: AsyncClient,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await create_order(client, user_token)
    await remove_from_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Tenet"])
    second = await create_order(client, user_token)
    await add_to_cart(client, user_token, movies["Inception"])

    response = await create_order(client, user_token)

    # No common movies with order 1, so order 2 was allowed.
    assert second.status_code == 201
    assert order_names(second.json()) == ["Tenet"]
    assert response.status_code == 409
    assert response.json() == in_pending_orders("1, 2")


async def test_canceled_order_does_not_block(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    user_id = await user_id_of(db_session)
    await save_order(
        db_session, user_id, [movies["Inception"]], OrderStatusEnum.CANCELED
    )

    response = await create_order(client, user_token)

    assert response.status_code == 201
    assert order_names(response.json()) == ["Inception"]
    assert response.json()["excluded"] == []


# --- Total ---


@pytest.mark.parametrize(
    ("second_price", "status_code"),
    [("0.01", 201), ("0.02", 409)],
    ids=["the largest total", "one cent above"],
)
async def test_total_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    refs: dict[str, int],
    second_price: str,
    status_code: int,
) -> None:
    first = await save_movie(db_session, refs, "Expensive", price="99999999.98")
    second = await save_movie(db_session, refs, "Cheap", price=second_price)
    await add_to_cart(client, user_token, first)
    await add_to_cart(client, user_token, second)

    response = await create_order(client, user_token)

    assert response.status_code == status_code
    if status_code == 201:
        assert response.json()["total_amount"] == "99999999.99"
    else:
        assert response.json() == TOTAL_TOO_LARGE
        assert await count_order_rows(db_session) == NO_ORDERS


# --- Parallel requests ---


async def test_double_click_creates_one_order(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])

    responses = await asyncio.gather(
        create_order(client, user_token), create_order(client, user_token)
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    assert await count_order_rows(db_session) == {"orders": 1, "items": 1}


async def test_waits_for_another_order_of_the_same_cart(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # Another request of the same user holds the cart (as "create order"
    # does until it is saved): this one must wait, not check too early.
    await add_to_cart(client, user_token, movies["Inception"])
    user_id = await user_id_of(db_session)
    async with async_sessionmaker(db_engine)() as other_request:
        await other_request.execute(
            select(Cart.id).where(Cart.user_id == user_id).with_for_update()
        )
        ordering = asyncio.create_task(create_order(client, user_token))
        await asyncio.sleep(0.5)
        assert not ordering.done()
        await other_request.commit()

    response = await ordering

    assert response.status_code == 201


async def test_waits_for_a_price_change_in_progress(
    client: AsyncClient,
    db_engine: AsyncEngine,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # The new price is saved but not committed yet. Without the lock on the
    # movies the order would silently take the old price.
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Tenet"])
    async with async_sessionmaker(db_engine)() as moderator:
        await moderator.execute(
            update(Movie)
            .where(Movie.id == movies["Inception"])
            .values(price=Decimal("1.00"))
        )
        ordering = asyncio.create_task(create_order(client, user_token))
        await asyncio.sleep(0.5)
        assert not ordering.done()
        await moderator.commit()

    response = await ordering

    assert response.status_code == 201
    order = response.json()
    assert [item["price_at_order"] for item in order["items"]] == ["1.00", "7.50"]
    assert order["total_amount"] == "8.50"


async def test_waits_for_a_movie_delete_in_progress(
    client: AsyncClient,
    db_engine: AsyncEngine,
    user_token: str,
    movies: dict[str, int],
) -> None:
    # The movie is deleted but not committed yet. Without the lock on the
    # movies the order would include it and the DB would refuse: a 500.
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Tenet"])
    async with async_sessionmaker(db_engine)() as moderator:
        await moderator.execute(delete(Movie).where(Movie.id == movies["Inception"]))
        ordering = asyncio.create_task(create_order(client, user_token))
        await asyncio.sleep(0.5)
        assert not ordering.done()
        await moderator.commit()

    response = await ordering

    assert response.status_code == 201
    assert order_names(response.json()) == ["Tenet"]
    assert response.json()["total_amount"] == "7.50"
