"""GET /movies?search=..."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.movies import Director, Star
from app.schemas.pagination import PAGE_SIZE
from tests.routers.movies.helpers import add_item, names, save_movie


@pytest.fixture
async def searchable(db_session: AsyncSession, refs: dict[str, int]) -> None:
    """Each word below is in only one place of one movie.

    "Delta" is the only movie directed by Christopher Nolan (refs).
    """
    campion = await add_item(db_session, Director, "Jane Campion")
    others = {"director_ids": [campion.id]}
    await save_movie(db_session, refs, "Night Train", **others)
    await save_movie(
        db_session, refs, "Blue", description="Lost in the OCEAN.", **others
    )
    await save_movie(db_session, refs, "Gamma", star_ids=[refs["star"]], **others)
    await save_movie(db_session, refs, "Delta")


@pytest.mark.parametrize(
    ("search", "expected"),
    [
        ("TRAI", "Night Train"),  # name
        ("cea", "Blue"),  # description
        ("caPRI", "Gamma"),  # star: Leonardo DiCaprio
        ("ola", "Delta"),  # director: Christopher Nolan
    ],
)
async def test_finds_part_of_each_field_in_any_case(
    client: AsyncClient, searchable: None, search: str, expected: str
) -> None:
    response = await client.get("/movies", params={"search": search})

    assert response.status_code == 200
    assert names(response.json()) == [expected]
    assert response.json()["total"] == 1


async def test_movie_matching_in_many_places_is_found_once(
    client: AsyncClient, db_session: AsyncSession, refs: dict[str, int]
) -> None:
    stars = [await add_item(db_session, Star, f"Max {n}") for n in (1, 2)]
    director = await add_item(db_session, Director, "Max Director")
    await save_movie(
        db_session,
        refs,
        "Max",
        description="Max and Max.",
        star_ids=[star.id for star in stars],
        director_ids=[director.id],
    )

    response = await client.get("/movies", params={"search": "max"})

    assert names(response.json()) == ["Max"]
    assert response.json()["total"] == 1


@pytest.mark.parametrize(
    ("search", "expected"), [("100%", "100% Love"), ("a_b", "a_b")]
)
async def test_percent_and_underscore_are_plain_characters(
    client: AsyncClient,
    db_session: AsyncSession,
    refs: dict[str, int],
    search: str,
    expected: str,
) -> None:
    # Without escaping "100%" would also match "1000 Days", "a_b" - "axb".
    for name in ("100% Love", "1000 Days", "a_b", "axb"):
        await save_movie(db_session, refs, name)

    response = await client.get("/movies", params={"search": search})

    assert names(response.json()) == [expected]


@pytest.mark.parametrize("search", ["", "   "])
async def test_empty_search_shows_all(
    client: AsyncClient, searchable: None, search: str
) -> None:
    response = await client.get("/movies", params={"search": search})

    assert response.status_code == 200
    assert response.json()["total"] == 4


async def test_too_long_search_returns_422(client: AsyncClient) -> None:
    response = await client.get("/movies", params={"search": "a" * 256})

    assert response.status_code == 422


async def test_search_works_with_filters_and_pages(
    client: AsyncClient, db_session: AsyncSession, refs: dict[str, int]
) -> None:
    for number in range(1, PAGE_SIZE + 3):
        await save_movie(db_session, refs, f"Found {number:02}", year=2000 + number)
    await save_movie(db_session, refs, "Found in Action", genre_ids=[refs["action"]])
    await save_movie(db_session, refs, "Other")

    response = await client.get(
        "/movies",
        params={"search": "found", "genre_id": refs["drama"], "page": 2},
    )

    assert names(response.json()) == ["Found 02", "Found 01"]
    assert response.json()["total"] == PAGE_SIZE + 2
