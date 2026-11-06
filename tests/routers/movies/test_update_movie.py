"""PATCH /movies/{movie_id}."""

import asyncio
from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import MAX_DB_ID, PERMISSION_DENIED
from app.models.movies import Director, Genre
from app.routers.movies import MOVIE_EXISTS, MOVIE_NOT_FOUND, UNKNOWN_IDS
from app.schemas.movies import MAX_LIST_IDS, MAX_YEARS_AHEAD
from tests.routers.movies.helpers import add_item, auth, count_rows, movie_body

MAX_YEAR = date.today().year + MAX_YEARS_AHEAD


async def saved_movie(client: AsyncClient, movie_id: int) -> dict[str, Any]:
    response = await client.get(f"/movies/{movie_id}")
    return response.json()


async def test_moderator_changes_only_sent_fields(
    client: AsyncClient, moderator_token: str, movie: dict[str, Any]
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}",
        json={"name": "  Inception 2  ", "price": "12.50", "imdb": 9.1},
        headers=auth(moderator_token),
    )

    assert response.status_code == 200
    expected = movie | {"name": "Inception 2", "price": "12.50", "imdb": 9.1}
    assert response.json() == expected
    assert await saved_movie(client, movie["id"]) == expected


async def test_sent_lists_replace_old_ones(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    movie: dict[str, Any],
) -> None:
    director = await add_item(db_session, Director, "Steven Spielberg")

    response = await client.patch(
        f"/movies/{movie['id']}",
        json={
            "genre_ids": [refs["action"]],
            "star_ids": [],
            "director_ids": [director.id],
        },
        headers=auth(moderator_token),
    )

    assert response.status_code == 200
    data = response.json()
    # The response shows the new lists, not what the session saw before.
    assert data["genres"] == [{"id": refs["action"], "name": "Action"}]
    assert data["stars"] == []
    assert data["directors"] == [{"id": director.id, "name": "Steven Spielberg"}]
    assert await count_rows(db_session) == {
        "movies": 1,
        "genres": 1,
        "stars": 0,
        "directors": 1,
    }
    assert await saved_movie(client, movie["id"]) == data


async def test_meta_score_and_gross_can_be_cleared(
    client: AsyncClient, moderator_token: str, movie: dict[str, Any]
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}",
        json={"meta_score": None, "gross": None},
        headers=auth(moderator_token),
    )

    assert response.status_code == 200
    assert response.json() == movie | {"meta_score": None, "gross": None}


async def test_empty_body_changes_nothing(
    client: AsyncClient, moderator_token: str, movie: dict[str, Any]
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}", json={}, headers=auth(moderator_token)
    )

    assert response.status_code == 200
    assert response.json() == movie


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "year",
        "time",
        "imdb",
        "votes",
        "description",
        "price",
        "certification_id",
        "genre_ids",
        "star_ids",
        "director_ids",
    ],
)
async def test_null_for_required_field_returns_422(
    client: AsyncClient, moderator_token: str, movie: dict[str, Any], field: str
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}", json={field: None}, headers=auth(moderator_token)
    )

    assert response.status_code == 422
    assert await saved_movie(client, movie["id"]) == movie


# The same rules as on create (one case per kind of rule).
@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"price": "9.999"},
        {"year": MAX_YEAR + 1},
        {"time": MAX_DB_ID + 1},
        {"meta_score": 100.1},
        {"certification_id": MAX_DB_ID + 1},
        {"genre_ids": []},
        {"director_ids": []},
        {"star_ids": list(range(1, MAX_LIST_IDS + 2))},
    ],
    ids=lambda changes: "{}={!s:.12}".format(*next(iter(changes.items()))),
)
async def test_invalid_values_return_422(
    client: AsyncClient,
    moderator_token: str,
    movie: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}", json=changes, headers=auth(moderator_token)
    )

    assert response.status_code == 422
    assert await saved_movie(client, movie["id"]) == movie


async def test_infinity_returns_422(
    client: AsyncClient, moderator_token: str, movie: dict[str, Any]
) -> None:
    # Sent as raw text: httpx refuses to write Infinity.
    response = await client.patch(
        f"/movies/{movie['id']}",
        content='{"gross": Infinity}',
        headers={**auth(moderator_token), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert await saved_movie(client, movie["id"]) == movie


async def test_unknown_movie_returns_404(
    client: AsyncClient,
    moderator_token: str,
    refs: dict[str, int],
    movie: dict[str, Any],
) -> None:
    # With a list: links of a missing movie must not even be tried (-> 400).
    response = await client.patch(
        f"/movies/{movie['id'] + 1}",
        json={"name": "Interstellar", "genre_ids": [refs["action"]]},
        headers=auth(moderator_token),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": MOVIE_NOT_FOUND}


async def test_id_out_of_db_range_returns_422(
    client: AsyncClient, moderator_token: str
) -> None:
    response = await client.patch(
        f"/movies/{MAX_DB_ID + 1}", json={}, headers=auth(moderator_token)
    )

    assert response.status_code == 422


async def test_plain_user_cannot_update_movie(
    client: AsyncClient, demoted_token: str, movie: dict[str, Any]
) -> None:
    response = await client.patch(
        f"/movies/{movie['id']}",
        json={"name": "Interstellar"},
        headers=auth(demoted_token),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": PERMISSION_DENIED}
    assert await saved_movie(client, movie["id"]) == movie


async def test_name_year_and_time_of_other_movie_return_409(
    client: AsyncClient,
    moderator_token: str,
    refs: dict[str, int],
    movie: dict[str, Any],
) -> None:
    response = await client.post(
        "/movies",
        json=movie_body(refs, name="Interstellar", year=2014, time=169),
        headers=auth(moderator_token),
    )
    other = response.json()

    response = await client.patch(
        f"/movies/{other['id']}",
        json={"name": movie["name"], "year": movie["year"], "time": movie["time"]},
        headers=auth(moderator_token),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": MOVIE_EXISTS}
    assert await saved_movie(client, other["id"]) == other


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        # The existing Drama ("drama" -> its id) is not reported.
        ({"genre_ids": ["drama", 999]}, "genre_ids [999]"),
        (
            {"certification_id": 999, "star_ids": [998]},
            "certification_id [999], star_ids [998]",
        ),
        ({"name": "Interstellar", "director_ids": [997]}, "director_ids [997]"),
    ],
    ids=["genres", "certification-and-stars", "directors"],
)
async def test_unknown_ids_return_400_and_change_nothing(
    client: AsyncClient,
    moderator_token: str,
    refs: dict[str, int],
    movie: dict[str, Any],
    changes: dict[str, Any],
    missing: str,
) -> None:
    if "genre_ids" in changes:
        genre_ids = [refs.get(id_, id_) for id_ in changes["genre_ids"]]
        changes = changes | {"genre_ids": genre_ids}

    response = await client.patch(
        f"/movies/{movie['id']}", json=changes, headers=auth(moderator_token)
    )

    assert response.status_code == 400
    assert response.json() == {"detail": UNKNOWN_IDS.format(ids=missing)}
    # The old links were deleted before the new ones failed: rolled back too.
    assert await saved_movie(client, movie["id"]) == movie


@pytest.mark.parametrize("same_lists", [True, False], ids=["same", "different"])
async def test_parallel_updates_of_genres_dont_mix(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    movie: dict[str, Any],
    same_lists: bool,
) -> None:
    comedy = await add_item(db_session, Genre, "Comedy")
    lists = [[refs["action"]], [refs["action"] if same_lists else comedy.id]]

    responses = await asyncio.gather(
        *(
            client.patch(
                f"/movies/{movie['id']}",
                json={"genre_ids": genre_ids},
                headers=auth(moderator_token),
            )
            for genre_ids in lists
        )
    )

    assert [r.status_code for r in responses] == [200, 200]
    # The last update wins as a whole: its list, not a mix of both.
    genres = (await saved_movie(client, movie["id"]))["genres"]
    assert [genre["id"] for genre in genres] in lists


async def test_update_during_delete_returns_404_or_200(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    refs: dict[str, int],
    movie: dict[str, Any],
) -> None:
    update, delete = await asyncio.gather(
        client.patch(
            f"/movies/{movie['id']}",
            json={"genre_ids": [refs["action"]]},
            headers=auth(moderator_token),
        ),
        client.delete(f"/movies/{movie['id']}", headers=auth(moderator_token)),
    )

    assert delete.status_code == 204
    assert update.status_code in (200, 404)
    assert (await count_rows(db_session))["genres"] == 0
