"""GET /orders: the user's own orders."""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID
from app.models.movies import Movie
from app.models.orders import OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL
from tests.routers.carts.helpers import add_to_cart, remove_from_cart
from tests.routers.orders.helpers import (
    create_order,
    list_orders,
    order_names,
    save_order,
    user_id_of,
)


def ids(page: dict) -> list[int]:
    return [order["id"] for order in page["items"]]


async def test_orders_are_listed_newest_first_with_details(
    client: AsyncClient, user_token: str, movies: dict[str, int]
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await add_to_cart(client, user_token, movies["Memento"])
    first = (await create_order(client, user_token)).json()
    await remove_from_cart(client, user_token, movies["Inception"])
    await remove_from_cart(client, user_token, movies["Memento"])
    await add_to_cart(client, user_token, movies["Tenet"])
    second = (await create_order(client, user_token)).json()

    response = await list_orders(client, user_token)

    assert response.status_code == 200
    # The same order as "create" returned, without its `excluded` list.
    del first["excluded"], second["excluded"]
    assert response.json() == {"items": [second, first], "total": 2, "page": 1}
    assert order_names(first) == ["Inception", "Memento"]


async def test_without_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/orders")

    assert response.status_code == 401


async def test_no_orders_is_an_empty_page(client: AsyncClient, user_token: str) -> None:
    response = await list_orders(client, user_token)

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1}


async def test_orders_of_other_users_are_not_shown(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    other_id = await user_id_of(db_session, OTHER_EMAIL)
    await save_order(db_session, other_id, [movies["Inception"]])
    mine = await save_order(db_session, user_id, [movies["Tenet"]])
    await save_order(db_session, other_id, [movies["Memento"]])

    response = await list_orders(client, user_token)

    assert ids(response.json()) == [mine]
    assert response.json()["total"] == 1


async def test_every_status_is_shown(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    for status in OrderStatusEnum:
        await save_order(db_session, user_id, [movies["Inception"]], status)

    response = await list_orders(client, user_token)

    statuses = [order["status"] for order in response.json()["items"]]
    assert statuses == ["canceled", "paid", "pending"]


async def test_prices_stay_as_they_were_when_ordered(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, user_token, movies["Inception"])
    await create_order(client, user_token)
    await db_session.execute(
        update(Movie)
        .where(Movie.id == movies["Inception"])
        .values(price=Decimal("500.00"))
    )
    await db_session.commit()

    response = await list_orders(client, user_token)

    order = response.json()["items"][0]
    assert order["items"][0]["price_at_order"] == "19.99"
    assert order["total_amount"] == "19.99"


async def test_ten_orders_per_page(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    user_id = await user_id_of(db_session)
    for _ in range(12):
        await save_order(
            db_session, user_id, [movies["Inception"]], OrderStatusEnum.CANCELED
        )

    first = (await list_orders(client, user_token)).json()
    second = (await list_orders(client, user_token, page=2)).json()
    third = (await list_orders(client, user_token, page=3)).json()

    assert ids(first) == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]
    assert ids(second) == [2, 1]
    assert second == {"items": second["items"], "total": 12, "page": 2}
    assert third == {"items": [], "total": 12, "page": 3}


async def test_page_out_of_db_range_is_422(
    client: AsyncClient, user_token: str
) -> None:
    last = await list_orders(client, user_token, page=MAX_DB_ID)
    too_big = await list_orders(client, user_token, page=MAX_DB_ID + 1)

    assert last.status_code == 200
    assert too_big.status_code == 422
