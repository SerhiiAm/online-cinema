"""POST /movies."""

import asyncio
import json
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID, PERMISSION_DENIED
from app.routers.movies import MOVIE_EXISTS, UNKNOWN_IDS
from app.schemas.movies import MAX_LIST_IDS, MAX_YEARS_AHEAD
from tests.routers.movies.helpers import NOTHING_SAVED, auth, count_rows, movie_body

MAX_YEAR = date.today().year + MAX_YEARS_AHEAD


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
        "directors": 1,
    }


@pytest.mark.parametrize("sent", ["missing", "empty"])
async def test_movie_without_stars_is_saved(
    client: AsyncClient, moderator_token: str, refs: dict[str, int], sent: str
) -> None:
    body = movie_body(refs)
    if sent == "empty":
        body["star_ids"] = []

    response = await client.post("/movies", json=body, headers=auth(moderator_token))

    assert response.status_code == 201
    assert response.json()["stars"] == []


@pytest.mark.parametrize("field", ["genre_ids", "director_ids"])
@pytest.mark.parametrize("sent", ["missing", "empty"])
async def test_movie_needs_a_genre_and_a_director(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    field: str,
    sent: str,
) -> None:
    body = movie_body(refs, **{field: []})
    if sent == "missing":
        del body[field]

    response = await client.post("/movies", json=body, headers=auth(moderator_token))

    assert response.status_code == 422
    assert await count_rows(db_session) == NOTHING_SAVED


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
    ("field", "value"),
    [("gross", "Infinity"), ("meta_score", "NaN"), ("imdb", "-Infinity")],
)
async def test_nan_and_infinity_numbers_return_422(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    field: str,
    value: str,
) -> None:
    # Valid JSON for Python, but Infinity would pass `gross >= 0` and be
    # saved. Sent as raw text: httpx refuses to write these values.
    body = json.dumps(movie_body(refs, **{field: 0}))
    body = body.replace(f'"{field}": 0', f'"{field}": {value}')

    response = await client.post(
        "/movies",
        content=body,
        headers={**auth(moderator_token), "Content-Type": "application/json"},
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
