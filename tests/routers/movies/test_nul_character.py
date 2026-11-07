"""Every text that is saved or searched in the DB rejects the NUL character:
PostgreSQL can't handle it, so without the check it's a 500."""

from typing import Any

import pytest
from httpx import AsyncClient, Response

from app.core.validators import NUL_NOT_ALLOWED
from tests.routers.movies.helpers import ReferenceCase, auth, movie_body

WITH_NUL = "a\x00b"


def assert_nul_rejected(response: Response) -> None:
    assert response.status_code == 422
    assert NUL_NOT_ALLOWED in response.json()["detail"][0]["msg"]


async def test_reference_name(
    client: AsyncClient, moderator_token: str, ref: ReferenceCase
) -> None:
    response = await client.post(
        ref.path, json={"name": WITH_NUL}, headers=auth(moderator_token)
    )

    assert_nul_rejected(response)


@pytest.mark.parametrize("field", ["name", "description"])
async def test_movie_create(
    client: AsyncClient, moderator_token: str, refs: dict[str, int], field: str
) -> None:
    response = await client.post(
        "/movies",
        json=movie_body(refs, **{field: WITH_NUL}),
        headers=auth(moderator_token),
    )

    assert_nul_rejected(response)


@pytest.mark.parametrize("field", ["name", "description"])
async def test_movie_update(
    client: AsyncClient, moderator_token: str, movie: dict[str, Any], field: str
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}",
        json={field: WITH_NUL},
        headers=auth(moderator_token),
    )

    assert_nul_rejected(response)


@pytest.mark.parametrize("path", ["/movies", "/stars", "/directors"])
async def test_search(client: AsyncClient, path: str) -> None:
    response = await client.get(path, params={"search": WITH_NUL})

    assert_nul_rejected(response)
