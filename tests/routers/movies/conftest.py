from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounts import UserGroupEnum
from tests.routers.accounts.helpers import set_group
from tests.routers.movies.helpers import (
    REFERENCE_CASES,
    ReferenceCase,
    auth,
    movie_body,
)

# `user_token` and `refs` are in tests/routers/conftest.py (the cart uses them too).


@pytest.fixture
async def moderator_token(db_session: AsyncSession, user_token: str) -> str:
    # The group is read from the DB on every request, so the same token
    # works as a moderator's right after the change.
    await set_group(db_session, UserGroupEnum.MODERATOR)
    return user_token


@pytest.fixture(params=REFERENCE_CASES, ids=lambda case: case.path.strip("/"))
def ref(request: pytest.FixtureRequest) -> ReferenceCase:
    """Runs a test once for each of genres, stars, directors, certifications."""
    return request.param


@pytest.fixture
async def movie(
    client: AsyncClient, moderator_token: str, refs: dict[str, int]
) -> dict[str, Any]:
    """A saved movie (Drama, DiCaprio, Nolan) as POST /movies returned it."""
    body = movie_body(
        refs, meta_score=74, gross=292_576_195.0, star_ids=[refs["star"]]
    )
    response = await client.post("/movies", json=body, headers=auth(moderator_token))
    assert response.status_code == 201
    return response.json()


@pytest.fixture
async def demoted_token(
    db_session: AsyncSession, moderator_token: str, movie: dict[str, Any]
) -> str:
    """The moderator who created `movie`, moved back to the `user` group."""
    await set_group(db_session, UserGroupEnum.USER)
    return moderator_token
