"""GET /movies (catalog)."""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID
from app.models.movies import Movie
from app.schemas.pagination import PAGE_SIZE
from tests.routers.movies.helpers import names, save_movie

ITEM_FIELDS = (
    "id",
    "name",
    "year",
    "time",
    "imdb",
    "votes",
    "price",
    "certification",
    "genres",
)


NEWEST_FIRST = ["Charlie", "Bravo", "Delta", "Alpha"]  # the `catalog` by year


@pytest.fixture
async def catalog(db_session: AsyncSession, refs: dict[str, int]) -> None:
    """Four movies, each sort column gives them a different order."""
    movies = {
        "Alpha": dict(year=1994, imdb=8.0, votes=500, price="5.00",
                      star_ids=[refs["star"]]),
        "Bravo": dict(year=2001, imdb=9.0, votes=100, price="7.00",
                      genre_ids=[refs["action"]]),
        "Charlie": dict(year=2010, imdb=7.0, votes=300, price="3.00",
                        genre_ids=[refs["drama"], refs["action"]],
                        star_ids=[refs["star"]]),
        "Delta": dict(year=1999, imdb=6.0, votes=900, price="9.00"),
    }
    for name, fields in movies.items():
        await save_movie(db_session, refs, name, **fields)


async def test_anyone_gets_short_movie_cards(
    client: AsyncClient, movie: dict[str, Any]
) -> None:
    response = await client.get("/movies")  # no token

    assert response.status_code == 200
    card = {field: movie[field] for field in ITEM_FIELDS}
    assert response.json() == {"items": [card], "total": 1, "page": 1}


async def test_pages(
    client: AsyncClient, db_session: AsyncSession, refs: dict[str, int]
) -> None:
    for number in range(1, PAGE_SIZE + 3):
        await save_movie(db_session, refs, f"Movie {number:02}", year=2000 + number)
    newest_first = [f"Movie {number:02}" for number in range(PAGE_SIZE + 2, 0, -1)]

    pages = [
        (await client.get("/movies", params={"page": number})).json()
        for number in (1, 2, 3)
    ]

    assert [names(page) for page in pages] == [
        newest_first[:PAGE_SIZE],
        newest_first[PAGE_SIZE:],
        [],
    ]
    assert [page["total"] for page in pages] == [PAGE_SIZE + 2] * 3
    assert [page["page"] for page in pages] == [1, 2, 3]


async def test_last_possible_page_is_empty(
    client: AsyncClient, movie: dict[str, Any]
) -> None:
    # The offset is far beyond a PostgreSQL integer.
    response = await client.get("/movies", params={"page": MAX_DB_ID})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 1, "page": MAX_DB_ID}


@pytest.mark.parametrize(
    ("column", "ascending"),
    [
        ("year", ["Alpha", "Delta", "Bravo", "Charlie"]),
        ("price", ["Charlie", "Alpha", "Bravo", "Delta"]),
        ("imdb", ["Delta", "Charlie", "Alpha", "Bravo"]),
        ("votes", ["Bravo", "Charlie", "Alpha", "Delta"]),
    ],
)
async def test_sort(
    client: AsyncClient, catalog: None, column: str, ascending: list[str]
) -> None:
    up = await client.get("/movies", params={"sort": column})
    down = await client.get("/movies", params={"sort": f"-{column}"})

    assert names(up.json()) == ascending
    assert names(down.json()) == ascending[::-1]


async def test_newest_first_by_default(client: AsyncClient, catalog: None) -> None:
    response = await client.get("/movies")

    assert names(response.json()) == NEWEST_FIRST


@pytest.mark.parametrize("sort", ["year", "-year"])
async def test_equal_values_are_ordered_by_id(
    client: AsyncClient, db_session: AsyncSession, refs: dict[str, int], sort: str
) -> None:
    for name in ("First", "Second", "Third"):
        await save_movie(db_session, refs, name, year=1994)
    # An updated row moves to the end of the table, so without the id
    # order the DB returns Second, Third, First.
    await db_session.execute(
        update(Movie).where(Movie.name == "First").values(price=Decimal("1.00"))
    )
    await db_session.commit()

    response = await client.get("/movies", params={"sort": sort})

    assert names(response.json()) == ["First", "Second", "Third"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"year_from": 1999}, ["Charlie", "Bravo", "Delta"]),
        ({"year_to": 1999}, ["Delta", "Alpha"]),
        ({"year_from": 1999, "year_to": 2001}, ["Bravo", "Delta"]),
        ({"year_from": 2001, "year_to": 1999}, []),
        ({"year_from": 0, "year_to": MAX_DB_ID}, NEWEST_FIRST),
        ({"imdb_min": 8}, ["Bravo", "Alpha"]),
        ({"imdb_min": 8, "year_from": 1995}, ["Bravo"]),
    ],
)
async def test_number_filters(
    client: AsyncClient, catalog: None, params: dict[str, int], expected: list[str]
) -> None:
    response = await client.get("/movies", params=params)

    assert names(response.json()) == expected
    assert response.json()["total"] == len(expected)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"genre_id": "action"}, ["Charlie", "Bravo"]),
        ({"star_id": "star"}, ["Charlie", "Alpha"]),
        ({"genre_id": "drama", "star_id": "star"}, ["Charlie", "Alpha"]),
        ({"genre_id": "action", "star_id": "star"}, ["Charlie"]),
    ],
)
async def test_genre_and_star_filters(
    client: AsyncClient,
    catalog: None,
    refs: dict[str, int],
    params: dict[str, str],
    expected: list[str],
) -> None:
    ids = {param: refs[key] for param, key in params.items()}

    response = await client.get("/movies", params=ids)

    assert names(response.json()) == expected
    assert response.json()["total"] == len(expected)


async def test_unknown_genre_gives_empty_page(
    client: AsyncClient, movie: dict[str, Any]
) -> None:
    response = await client.get("/movies", params={"genre_id": MAX_DB_ID})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1}


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "name"},
        {"page": 0},
        {"page": MAX_DB_ID + 1},
        {"year_from": -1},
        {"year_to": MAX_DB_ID + 1},
        {"imdb_min": "nan"},
        {"imdb_min": "inf"},
        {"imdb_min": 10.1},
        {"genre_id": 0},
        {"star_id": MAX_DB_ID + 1},
    ],
)
async def test_invalid_query_returns_422(
    client: AsyncClient, params: dict[str, Any]
) -> None:
    response = await client.get("/movies", params=params)

    assert response.status_code == 422
