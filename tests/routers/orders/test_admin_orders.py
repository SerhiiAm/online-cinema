"""GET /orders/all: an admin sees the orders of all users, with filters."""

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID
from app.models.accounts import UserGroupEnum
from app.models.orders import Order, OrderStatusEnum
from tests.routers.accounts.helpers import OTHER_EMAIL, set_group
from tests.routers.carts.helpers import add_to_cart
from tests.routers.orders.helpers import (
    create_order,
    list_all_orders,
    save_order,
    user_id_of,
)


def ids(page: dict) -> list[int]:
    return [order["id"] for order in page["items"]]


async def save_order_at(
    db: AsyncSession, user_id: int, movie_id: int, created_at: str
) -> int:
    """Saves an order as if it was created at the given moment."""
    order_id = await save_order(db, user_id, [movie_id])
    await db.execute(
        update(Order)
        .where(Order.id == order_id)
        .values(created_at=datetime.fromisoformat(created_at))
    )
    await db.commit()
    return order_id


@pytest.fixture
async def user_ids(db_session: AsyncSession, other_token: str) -> dict[str, int]:
    """Ids of the two users; "admin" is the one behind `admin_token`."""
    return {
        "admin": await user_id_of(db_session),
        "other": await user_id_of(db_session, OTHER_EMAIL),
    }


async def test_admin_sees_orders_of_all_users_newest_first(
    client: AsyncClient,
    admin_token: str,
    other_token: str,
    user_ids: dict[str, int],
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, other_token, movies["Tenet"])
    theirs = (await create_order(client, other_token)).json()
    await add_to_cart(client, admin_token, movies["Inception"])
    mine = (await create_order(client, admin_token)).json()

    response = await list_all_orders(client, admin_token)

    assert response.status_code == 200
    # The same orders as their owners got, plus whose they are.
    del theirs["excluded"], mine["excluded"]
    assert response.json() == {
        "items": [
            {**mine, "user_id": user_ids["admin"]},
            {**theirs, "user_id": user_ids["other"]},
        ],
        "total": 2,
        "page": 1,
    }


async def test_without_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/orders/all")

    assert response.status_code == 401


@pytest.mark.parametrize("group", [UserGroupEnum.USER, UserGroupEnum.MODERATOR])
async def test_only_admins_are_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    group: UserGroupEnum,
) -> None:
    await set_group(db_session, group)

    response = await list_all_orders(client, user_token)

    assert response.status_code == 403


async def test_filter_by_user(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    user_ids: dict[str, int],
    movies: dict[str, int],
) -> None:
    await save_order(db_session, user_ids["admin"], [movies["Inception"]])
    first = await save_order(db_session, user_ids["other"], [movies["Inception"]])
    second = await save_order(db_session, user_ids["other"], [movies["Tenet"]])

    response = await list_all_orders(
        client, admin_token, user_id=str(user_ids["other"])
    )

    assert ids(response.json()) == [second, first]
    assert response.json()["total"] == 2


async def test_unknown_user_is_an_empty_page(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    user_ids: dict[str, int],
    movies: dict[str, int],
) -> None:
    await save_order(db_session, user_ids["other"], [movies["Inception"]])

    response = await list_all_orders(client, admin_token, user_id=str(MAX_DB_ID))

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1}


async def test_user_id_out_of_db_range_is_422(
    client: AsyncClient, admin_token: str
) -> None:
    response = await list_all_orders(client, admin_token, user_id=str(MAX_DB_ID + 1))

    assert response.status_code == 422


@pytest.mark.parametrize("status", list(OrderStatusEnum))
async def test_filter_by_status(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    user_ids: dict[str, int],
    movies: dict[str, int],
    status: OrderStatusEnum,
) -> None:
    saved = {
        each: await save_order(
            db_session, user_ids["other"], [movies["Inception"]], each
        )
        for each in OrderStatusEnum
    }

    response = await list_all_orders(client, admin_token, status=status.value)

    assert ids(response.json()) == [saved[status]]
    assert response.json()["items"][0]["status"] == status.value


# --- Dates ---


@pytest.fixture
async def dated(
    db_session: AsyncSession, user_ids: dict[str, int], movies: dict[str, int]
) -> dict[str, int]:
    """Orders created around the borders of January 10 (UTC)."""
    moments = {
        "last moment of the 9th": "2026-01-09T23:59:59.999999+00:00",
        # Already January 10 in Kyiv, but still the 9th in UTC.
        "the 10th in Kyiv only": "2026-01-10T01:30:00+02:00",
        "first moment of the 10th": "2026-01-10T00:00:00+00:00",
        "last moment of the 10th": "2026-01-10T23:59:59.999999+00:00",
        "first moment of the 11th": "2026-01-11T00:00:00+00:00",
    }
    return {
        name: await save_order_at(
            db_session, user_ids["other"], movies["Inception"], moment
        )
        for name, moment in moments.items()
    }


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (
            {"date_from": "2026-01-10", "date_to": "2026-01-10"},
            ["last moment of the 10th", "first moment of the 10th"],
        ),
        (
            {"date_from": "2026-01-10"},
            [
                "first moment of the 11th",
                "last moment of the 10th",
                "first moment of the 10th",
            ],
        ),
        (
            {"date_to": "2026-01-09"},
            ["the 10th in Kyiv only", "last moment of the 9th"],
        ),
        ({"date_from": "2026-01-11", "date_to": "2026-01-09"}, []),
    ],
    ids=["one day", "from a day", "up to a day", "from after to"],
)
async def test_filter_by_dates_takes_whole_utc_days(
    client: AsyncClient,
    admin_token: str,
    dated: dict[str, int],
    filters: dict[str, str],
    expected: list[str],
) -> None:
    response = await list_all_orders(client, admin_token, **filters)

    assert response.status_code == 200
    assert ids(response.json()) == [dated[name] for name in expected]
    assert response.json()["total"] == len(expected)


# --- Filters together, pages ---


async def test_filters_are_combined(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    user_ids: dict[str, int],
    dated: dict[str, int],
    movies: dict[str, int],
) -> None:
    # Each of these fails exactly one of the three filters.
    await save_order_at(
        db_session, user_ids["admin"], movies["Tenet"], "2026-01-10T12:00:00+00:00"
    )
    await db_session.execute(
        update(Order)
        .where(Order.id == dated["first moment of the 10th"])
        .values(status=OrderStatusEnum.PAID)
    )
    await db_session.commit()

    response = await list_all_orders(
        client,
        admin_token,
        user_id=str(user_ids["other"]),
        status="pending",
        date_from="2026-01-10",
        date_to="2026-01-10",
    )

    assert ids(response.json()) == [dated["last moment of the 10th"]]
    assert response.json()["total"] == 1


async def test_pages_and_total_follow_the_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_token: str,
    user_ids: dict[str, int],
    movies: dict[str, int],
) -> None:
    for _ in range(12):
        await save_order(
            db_session, user_ids["other"], [movies["Inception"]], OrderStatusEnum.PAID
        )
    await save_order(db_session, user_ids["other"], [movies["Tenet"]])

    first = (await list_all_orders(client, admin_token, status="paid")).json()
    second = (
        await list_all_orders(client, admin_token, status="paid", page="2")
    ).json()

    assert ids(first) == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]
    assert ids(second) == [2, 1]
    assert first["total"] == second["total"] == 12
    assert second["page"] == 2
