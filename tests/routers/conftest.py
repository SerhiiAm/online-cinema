"""Fixtures shared by the movies, carts, orders and payments tests."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from stripe.checkout import SessionService

from app.models.accounts import UserGroupEnum
from app.models.movies import Certification, Director, Genre, Star
from tests.routers.accounts.helpers import (
    OTHER_EMAIL,
    activate,
    get_token,
    login,
    register,
    set_group,
)
from tests.routers.movies.helpers import add_item, save_movie


@pytest.fixture
async def user_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Access token of an active user from the plain `user` group."""
    await register(client)
    activation_token = await get_token(db_session)
    await activate(client, activation_token.token)
    response = await login(client)
    return response.json()["access_token"]


@pytest.fixture
async def other_token(
    client: AsyncClient, db_session: AsyncSession, user_token: str
) -> str:
    """Access token of a second active user (needs `user_token` first)."""
    await register(client, OTHER_EMAIL)
    activation_token = await get_token(db_session, OTHER_EMAIL)
    await activate(client, activation_token.token, OTHER_EMAIL)
    response = await login(client, OTHER_EMAIL)
    return response.json()["access_token"]


@pytest.fixture
async def moderator_token(db_session: AsyncSession, user_token: str) -> str:
    # The group is read from the DB on every request, so the same token
    # works as a moderator's right after the change.
    await set_group(db_session, UserGroupEnum.MODERATOR)
    return user_token


@pytest.fixture
async def admin_token(db_session: AsyncSession, user_token: str) -> str:
    await set_group(db_session, UserGroupEnum.ADMIN)
    return user_token


class FakeStripe:
    """Stands in for Stripe's Checkout Sessions API.

    Our own code that builds the requests still runs; `created` keeps what
    it sent, `sessions` what "Stripe" knows (a test may change a status).
    """

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.sessions: dict[str, SimpleNamespace] = {}
        # Set to a stripe.StripeError to make every call fail with it.
        self.error: Exception | None = None
        # A pause inside "create", to let a parallel request catch up.
        self.delay = 0.0

    async def create(self, params: dict[str, Any], options: object = None):
        if self.error is not None:
            raise self.error
        await asyncio.sleep(self.delay)
        self.created.append(params)
        number = len(self.created)
        session = SimpleNamespace(
            id=f"cs_test_{number}",
            url=f"https://checkout.stripe.test/{number}",
            status="open",
        )
        self.sessions[session.id] = session
        return session

    async def retrieve(self, session_id: str, params: object = None,
                       options: object = None):
        if self.error is not None:
            raise self.error
        return self.sessions[session_id]


@pytest.fixture
def fake_stripe(monkeypatch: pytest.MonkeyPatch) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(SessionService, "create_async", staticmethod(fake.create))
    monkeypatch.setattr(
        SessionService, "retrieve_async", staticmethod(fake.retrieve)
    )
    return fake


@pytest.fixture
async def refs(db_session: AsyncSession) -> dict[str, int]:
    """Ids of a certification, two genres, a star and a director."""
    items = {
        "certification": await add_item(db_session, Certification, "PG-13"),
        "drama": await add_item(db_session, Genre, "Drama"),
        "action": await add_item(db_session, Genre, "Action"),
        "star": await add_item(db_session, Star, "Leonardo DiCaprio"),
        "director": await add_item(db_session, Director, "Christopher Nolan"),
    }
    return {key: item.id for key, item in items.items()}


@pytest.fixture
async def movies(db_session: AsyncSession, refs: dict[str, int]) -> dict[str, int]:
    """Ids of three saved movies by name."""
    return {
        "Inception": await save_movie(
            db_session,
            refs,
            "Inception",
            price="19.99",
            genre_ids=[refs["drama"], refs["action"]],
        ),
        "Memento": await save_movie(db_session, refs, "Memento", price="5.01"),
        "Tenet": await save_movie(db_session, refs, "Tenet", price="7.50"),
    }
