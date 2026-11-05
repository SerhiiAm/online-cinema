"""POST /movies."""

import asyncio
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID, PERMISSION_DENIED
from app.models.movies import (
    Certification,
    Director,
    Genre,
    Movie,
    Star,
    movie_directors,
    movie_genres,
    movie_stars,
)
from app.routers.movies import MOVIE_EXISTS, UNKNOWN_IDS
from app.schemas.movies import MAX_LIST_IDS, MAX_YEARS_AHEAD
from tests.routers.movies.helpers import add_item, auth

MAX_YEAR = date.today().year + MAX_YEARS_AHEAD


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


def movie_body(refs: dict[str, int], **changes: Any) -> dict[str, Any]:
    body = {
        "name": "Inception",
        "year": 2010,
        "time": 148,
        "imdb": 8.8,
        "votes": 2_000_000,
        "description": "A thief who steals secrets through dreams.",
        "price": "9.99",
        "certification_id": refs["certification"],
        "genre_ids": [refs["drama"]],
    }
    return body | changes


async def count_rows(db: AsyncSession) -> dict[str, int]:
    await db.rollback()  # see what other sessions committed
    tables = {
        "movies": Movie.__table__,
        "genres": movie_genres,
        "stars": movie_stars,
        "directors": movie_directors,
    }
    return {
        name: await db.scalar(select(func.count()).select_from(table))
        for name, table in tables.items()
    }


NOTHING_SAVED = {"movies": 0, "genres": 0, "stars": 0, "directors": 0}


async def test_moderator_creates_movie(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
) -> None:
    body = movie_body(
        refs,
        name="  Inception  ",
        meta_score=74,
        gross=292_576_195.0,
        genre_ids=[refs["drama"], refs["action"], refs["drama"]],
        star_ids=[refs["star"]],
        director_ids=[refs["director"]],
    )

    response = await client.post("/movies", json=body, headers=auth(moderator_token))

    assert response.status_code == 201
    data = response.json()
    assert data == {
        "id": data["id"],
        "uuid": data["uuid"],
        "name": "Inception",
        "year": 2010,
        "time": 148,
        "imdb": 8.8,
        "votes": 2_000_000,
        "meta_score": 74.0,
        "gross": 292_576_195.0,
        "description": "A thief who steals secrets through dreams.",
        "price": "9.99",
        "certification": {"id": refs["certification"], "name": "PG-13"},
        "genres": [
            {"id": refs["action"], "name": "Action"},
            {"id": refs["drama"], "name": "Drama"},
        ],
        "stars": [{"id": refs["star"], "name": "Leonardo DiCaprio"}],
        "directors": [{"id": refs["director"], "name": "Christopher Nolan"}],
    }
    # The repeated Drama is linked once.
    assert await count_rows(db_session) == {
        "movies": 1,
        "genres": 2,
        "stars": 1,
        "directors": 1,
    }


async def test_plain_user_cannot_create_movie(
    client: AsyncClient,
    db_session: AsyncSession,
    user_token: str,
    refs: dict[str, int],
) -> None:
    response = await client.post(
        "/movies", json=movie_body(refs), headers=auth(user_token)
    )

    assert response.status_code == 403
    assert response.json() == {"detail": PERMISSION_DENIED}
    assert await count_rows(db_session) == NOTHING_SAVED


async def test_same_name_year_and_time_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
) -> None:
    await client.post("/movies", json=movie_body(refs), headers=auth(moderator_token))

    response = await client.post(
        "/movies",
        json=movie_body(refs, name=" Inception "),
        headers=auth(moderator_token),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": MOVIE_EXISTS}
    assert (await count_rows(db_session))["movies"] == 1


async def test_parallel_create_of_same_movie_saves_one(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
) -> None:
    responses = await asyncio.gather(
        *(
            client.post(
                "/movies", json=movie_body(refs), headers=auth(moderator_token)
            )
            for _ in range(2)
        )
    )

    assert sorted(r.status_code for r in responses) == [201, 409]
    assert await count_rows(db_session) == {
        "movies": 1,
        "genres": 1,
        "stars": 0,
        "directors": 0,
    }


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"certification_id": 999}, "certification_id [999]"),
        # The existing Drama ("drama" -> its id) is not reported.
        (
            {"genre_ids": ["drama", 999, 998], "star_ids": [997]},
            "genre_ids [999, 998], star_ids [997]",
        ),
        ({"director_ids": [996]}, "director_ids [996]"),
    ],
    ids=["certification", "genres-and-stars", "directors"],
)
async def test_unknown_ids_return_400_and_save_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    changes: dict[str, Any],
    missing: str,
) -> None:
    if "genre_ids" in changes:
        genre_ids = [refs.get(id_, id_) for id_ in changes["genre_ids"]]
        changes = changes | {"genre_ids": genre_ids}

    response = await client.post(
        "/movies", json=movie_body(refs, **changes), headers=auth(moderator_token)
    )

    assert response.status_code == 400
    assert response.json() == {"detail": UNKNOWN_IDS.format(ids=missing)}
    # The movie row was inserted before the links failed: rolled back too.
    assert await count_rows(db_session) == NOTHING_SAVED


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"name": "x" * 256},
        {"description": "   "},
        {"price": "9.999"},
        {"price": "0"},
        {"price": "100000000"},
        {"year": 1887},
        {"year": MAX_YEAR + 1},
        {"time": 0},
        {"time": MAX_DB_ID + 1},
        {"votes": -1},
        {"votes": MAX_DB_ID + 1},
        {"imdb": 10.1},
        {"meta_score": 100.1},
        {"gross": -1},
        {"certification_id": MAX_DB_ID + 1},
        {"genre_ids": [0]},
        {"star_ids": [MAX_DB_ID + 1]},
        {"director_ids": list(range(1, MAX_LIST_IDS + 2))},
    ],
    ids=lambda changes: "{}={!s:.12}".format(*next(iter(changes.items()))),
)
async def test_invalid_values_return_422(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    changes: dict[str, Any],
) -> None:
    response = await client.post(
        "/movies", json=movie_body(refs, **changes), headers=auth(moderator_token)
    )

    assert response.status_code == 422
    assert await count_rows(db_session) == NOTHING_SAVED


@pytest.mark.parametrize(
    "changes",
    [
        {
            "year": MAX_YEAR,
            "time": MAX_DB_ID,
            "votes": MAX_DB_ID,
            "imdb": 10,
            "meta_score": 100,
            "price": "99999999.99",
        },
        {
            "year": 1888,
            "time": 1,
            "votes": 0,
            "imdb": 0,
            "meta_score": 0,
            "gross": 0,
            "price": "0.01",
        },
    ],
    ids=["max", "min"],
)
async def test_edge_values_are_saved(
    client: AsyncClient,
    moderator_token: str,
    refs: dict[str, int],
    changes: dict[str, Any],
) -> None:
    response = await client.post(
        "/movies", json=movie_body(refs, **changes), headers=auth(moderator_token)
    )

    assert response.status_code == 201
    data = response.json()
    assert {key: data[key] for key in changes} == changes
