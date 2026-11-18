"""GET /payments: a user sees their own payments."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.routers.accounts.helpers import OTHER_EMAIL
from tests.routers.orders.helpers import save_order, user_id_of
from tests.routers.payments.helpers import (
    get_payments,
    list_payments,
    save_payment,
    send_webhook,
    session_event,
)


def ids(page: dict) -> list[int]:
    return [payment["id"] for payment in page["items"]]


async def test_user_sees_only_own_payments_newest_first(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    me = await user_id_of(db_session)
    other = await user_id_of(db_session, OTHER_EMAIL)
    first = await save_payment(
        db_session, me, movies["Inception"], created_at="2026-01-10T12:00:00+00:00"
    )
    await save_payment(db_session, other, movies["Inception"])
    second = await save_payment(
        db_session, me, movies["Tenet"], created_at="2026-01-11T08:30:00+00:00"
    )
    orders = {each.id: each.order_id for each in await get_payments(db_session)}

    response = await list_payments(client, user_token)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": second,
                "created_at": "2026-01-11T08:30:00Z",
                "amount": "9.99",
                "status": "successful",
                "order_id": orders[second],
            },
            {
                "id": first,
                "created_at": "2026-01-10T12:00:00Z",
                "amount": "9.99",
                "status": "successful",
                "order_id": orders[first],
            },
        ],
        "total": 2,
        "page": 1,
    }


async def test_payment_confirmed_by_stripe_is_listed(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    order_id = await save_order(
        db_session, await user_id_of(db_session), [movies["Inception"]]
    )
    await send_webhook(client, session_event(order_id, amount_total=999))

    response = await list_payments(client, user_token)

    [payment] = response.json()["items"]
    assert payment["order_id"] == order_id
    assert payment["amount"] == "9.99"
    assert payment["status"] == "successful"


async def test_no_payments_is_an_empty_page(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    other = await user_id_of(db_session, OTHER_EMAIL)
    await save_payment(db_session, other, movies["Inception"])

    response = await list_payments(client, user_token)

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1}


async def test_without_token_is_401(client: AsyncClient) -> None:
    response = await client.get("/payments")

    assert response.status_code == 401


async def test_pages_and_total(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    movies: dict[str, int],
) -> None:
    me = await user_id_of(db_session)
    saved = [await save_payment(db_session, me, movies["Inception"]) for _ in range(12)]

    first = (await list_payments(client, user_token)).json()
    second = (await list_payments(client, user_token, page="2")).json()

    assert ids(first) == saved[:1:-1]  # the 10 newest
    assert ids(second) == saved[1::-1]
    assert first["total"] == second["total"] == 12
    assert second["page"] == 2
