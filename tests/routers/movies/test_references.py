"""Genres, stars, directors and certifications: every test runs for all four
(the `ref` fixture), because each one is wired with its own model and its
own way of counting movies."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID, PERMISSION_DENIED
from app.routers.references import IN_USE, NAME_TAKEN, NOT_FOUND
from app.schemas.pagination import PAGE_SIZE
from tests.routers.movies.helpers import (
    PAGINATED_CASES,
    ReferenceCase,
    add_item,
    add_movie,
    auth,
    get_names,
)

# --- List ---


async def test_list_is_public_sorted_and_counts_movies(
    client: AsyncClient, db_session: AsyncSession, ref: ReferenceCase
) -> None:
    used_twice = await add_item(db_session, ref.model, "Beta")
    used_once = await add_item(db_session, ref.model, "Alpha")
    unused = await add_item(db_session, ref.model, "Gamma")
    await add_movie(db_session, "Movie 1", used_twice)
    await add_movie(db_session, "Movie 2", used_twice)
    await add_movie(db_session, "Movie 3", used_once)

    response = await client.get(ref.path)

    assert response.status_code == 200
    items = [
        {"id": used_once.id, "name": "Alpha", "movie_count": 1},
        {"id": used_twice.id, "name": "Beta", "movie_count": 2},
        {"id": unused.id, "name": "Gamma", "movie_count": 0},
    ]
    if ref.paginated:
        assert response.json() == {"items": items, "total": 3, "page": 1}
    else:
        assert response.json() == items


@pytest.mark.parametrize("ref", PAGINATED_CASES, ids=lambda case: case.path.strip("/"))
async def test_pages(
    client: AsyncClient, db_session: AsyncSession, ref: ReferenceCase
) -> None:
    for number in range(PAGE_SIZE + 2, 0, -1):  # added in reverse name order
        await add_item(db_session, ref.model, f"Name {number:02}")
    by_name = [f"Name {number:02}" for number in range(1, PAGE_SIZE + 3)]

    pages = [
        (await client.get(ref.path, params={"page": number})).json()
        for number in (1, 2, 3)
    ]

    assert [[item["name"] for item in page["items"]] for page in pages] == [
        by_name[:PAGE_SIZE],
        by_name[PAGE_SIZE:],
        [],
    ]
    assert [page["total"] for page in pages] == [PAGE_SIZE + 2] * 3
    assert [page["page"] for page in pages] == [1, 2, 3]


@pytest.mark.parametrize("ref", PAGINATED_CASES, ids=lambda case: case.path.strip("/"))
async def test_last_possible_page_is_empty(
    client: AsyncClient, db_session: AsyncSession, ref: ReferenceCase
) -> None:
    await add_item(db_session, ref.model, "Alpha")

    # The offset is far beyond a PostgreSQL integer.
    response = await client.get(ref.path, params={"page": MAX_DB_ID})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 1, "page": MAX_DB_ID}


@pytest.mark.parametrize("ref", PAGINATED_CASES, ids=lambda case: case.path.strip("/"))
@pytest.mark.parametrize("page", [0, MAX_DB_ID + 1])
async def test_invalid_page_returns_422(
    client: AsyncClient, ref: ReferenceCase, page: int
) -> None:
    response = await client.get(ref.path, params={"page": page})

    assert response.status_code == 422


# --- Create ---


async def test_moderator_creates_item_with_trimmed_name(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    response = await client.post(
        ref.path, json={"name": "  Drama  "}, headers=auth(moderator_token)
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": response.json()["id"],
        "name": "Drama",
        "movie_count": 0,
    }
    assert await get_names(db_session, ref.model) == ["Drama"]


@pytest.mark.parametrize("name", ["   ", "x" * 256])
async def test_blank_or_too_long_name_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
    name: str,
) -> None:
    response = await client.post(
        ref.path, json={"name": name}, headers=auth(moderator_token)
    )

    assert response.status_code == 422
    assert await get_names(db_session, ref.model) == []


async def test_create_same_name_in_other_case_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    await add_item(db_session, ref.model, "Drama")

    response = await client.post(
        ref.path, json={"name": " dRAMA"}, headers=auth(moderator_token)
    )

    assert response.status_code == 409
    assert response.json() == {"detail": NAME_TAKEN.format(title=ref.title)}
    assert await get_names(db_session, ref.model) == ["Drama"]


async def test_parallel_create_of_same_name_saves_one(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    responses = await asyncio.gather(
        *(
            client.post(ref.path, json={"name": name}, headers=auth(moderator_token))
            for name in ("Action", "ACTION")
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert len(await get_names(db_session, ref.model)) == 1


# --- Rename ---


async def test_moderator_renames_item(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    item = await add_item(db_session, ref.model, "Dramma")
    await add_movie(db_session, "Movie 1", item)

    response = await client.patch(
        f"{ref.path}/{item.id}", json={"name": "Drama "}, headers=auth(moderator_token)
    )

    assert response.status_code == 200
    assert response.json() == {"id": item.id, "name": "Drama", "movie_count": 1}
    assert await get_names(db_session, ref.model) == ["Drama"]


async def test_rename_to_taken_name_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    await add_item(db_session, ref.model, "Drama")
    item = await add_item(db_session, ref.model, "Comedy")

    response = await client.patch(
        f"{ref.path}/{item.id}", json={"name": "DRAMA"}, headers=auth(moderator_token)
    )

    assert response.status_code == 409
    assert response.json() == {"detail": NAME_TAKEN.format(title=ref.title)}
    assert await get_names(db_session, ref.model) == ["Drama", "Comedy"]


# --- Delete ---


async def test_moderator_deletes_unused_item(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    item = await add_item(db_session, ref.model, "Drama")
    await add_item(db_session, ref.model, "Comedy")

    response = await client.delete(
        f"{ref.path}/{item.id}", headers=auth(moderator_token)
    )

    assert response.status_code == 204
    assert await get_names(db_session, ref.model) == ["Comedy"]


async def test_delete_of_used_item_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    ref: ReferenceCase,
) -> None:
    item = await add_item(db_session, ref.model, "Drama")
    await add_movie(db_session, "Movie 1", item)
    await add_movie(db_session, "Movie 2", item)

    response = await client.delete(
        f"{ref.path}/{item.id}", headers=auth(moderator_token)
    )

    assert response.status_code == 409
    assert response.json() == {"detail": IN_USE.format(title=ref.title, count=2)}
    assert "Drama" in await get_names(db_session, ref.model)


# --- Shared rules for writes ---


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_unknown_item_returns_404(
    client: AsyncClient, moderator_token: str, ref: ReferenceCase, method: str
) -> None:
    response = await client.request(
        method, f"{ref.path}/999", json={"name": "Drama"}, headers=auth(moderator_token)
    )

    assert response.status_code == 404
    assert response.json() == {"detail": NOT_FOUND.format(title=ref.title)}


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_id_out_of_db_range_returns_422(
    client: AsyncClient, moderator_token: str, ref: ReferenceCase, method: str
) -> None:
    # Bigger than a PostgreSQL integer: used to reach the DB and fail with 500.
    response = await client.request(
        method,
        f"{ref.path}/{MAX_DB_ID + 1}",
        json={"name": "Drama"},
        headers=auth(moderator_token),
    )

    assert response.status_code == 422


@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_plain_user_cannot_change_items(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    ref: ReferenceCase,
    method: str,
) -> None:
    item = await add_item(db_session, ref.model, "Drama")
    path = ref.path if method == "POST" else f"{ref.path}/{item.id}"

    response = await client.request(
        method, path, json={"name": "Comedy"}, headers=auth(user_token)
    )

    assert response.status_code == 403
    assert response.json() == {"detail": PERMISSION_DENIED}
    assert await get_names(db_session, ref.model) == ["Drama"]
