"""DELETE /movies/{id} for a movie that is in users' carts."""

import asyncio

from httpx import AsyncClient, Response
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.dependencies import MAX_DB_ID
from app.crud.movies import get_movie
from app.models.carts import Cart, CartItem
from tests.routers.accounts.helpers import get_user
from tests.routers.carts.helpers import OTHER_EMAIL, add_to_cart, cart_names, get_cart
from tests.routers.movies.helpers import auth

HOW_TO_FORCE = " Send force=true to delete it anyway and remove it from users' carts."
IN_ONE_CART = "This movie is in 1 cart." + HOW_TO_FORCE
IN_TWO_CARTS = "This movie is in 2 carts." + HOW_TO_FORCE


async def delete_movie(
    client: AsyncClient, token: str, movie_id: int, force: bool | None = None
) -> Response:
    params = {} if force is None else {"force": force}
    return await client.delete(
        f"/movies/{movie_id}", params=params, headers=auth(token)
    )


async def movie_exists(db: AsyncSession, movie_id: int) -> bool:
    await db.rollback()  # see what other sessions committed
    return await get_movie(db, movie_id) is not None


async def test_movie_in_carts_is_409_without_force(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, moderator_token, movies["Inception"])
    await add_to_cart(client, other_token, movies["Inception"])

    response = await delete_movie(client, moderator_token, movies["Inception"])

    assert response.status_code == 409
    assert response.json() == {"detail": IN_TWO_CARTS}
    assert await movie_exists(db_session, movies["Inception"])
    theirs = (await get_cart(client, other_token)).json()
    assert cart_names(theirs) == ["Inception"]


async def test_force_deletes_and_removes_from_carts(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    await add_to_cart(client, moderator_token, movies["Inception"])
    await add_to_cart(client, other_token, movies["Inception"])
    await add_to_cart(client, other_token, movies["Tenet"])

    response = await delete_movie(
        client, moderator_token, movies["Inception"], force=True
    )

    assert response.status_code == 204
    assert not await movie_exists(db_session, movies["Inception"])
    mine = (await get_cart(client, moderator_token)).json()
    theirs = (await get_cart(client, other_token)).json()
    assert mine == {"items": [], "total": "0"}
    assert cart_names(theirs) == ["Tenet"]
    assert theirs["total"] == "7.50"


async def test_movie_not_in_carts_is_deleted_without_force(
    client: AsyncClient,
    db_session: AsyncSession,
    moderator_token: str,
    movies: dict[str, int],
) -> None:
    # Another movie in a cart must not block this one.
    await add_to_cart(client, moderator_token, movies["Tenet"])

    response = await delete_movie(client, moderator_token, movies["Inception"])

    assert response.status_code == 204
    assert not await movie_exists(db_session, movies["Inception"])


async def test_force_on_unknown_movie_is_404(
    client: AsyncClient, moderator_token: str
) -> None:
    response = await delete_movie(client, moderator_token, MAX_DB_ID, force=True)

    assert response.status_code == 404
    assert response.json() == {"detail": "Movie not found."}


async def test_delete_waits_for_an_add_in_progress(
    client: AsyncClient,
    db_engine: AsyncEngine,
    db_session: AsyncSession,
    moderator_token: str,
    other_token: str,
    movies: dict[str, int],
) -> None:
    # A user's add is saved but not committed yet when the moderator
    # deletes. Without the row lock the delete wouldn't see that cart,
    # would answer 204 and silently remove the movie the user just added.
    other_id = (await get_user(db_session, OTHER_EMAIL)).id
    async with async_sessionmaker(db_engine)() as adding:
        cart_id = await adding.scalar(
            insert(Cart).values(user_id=other_id).returning(Cart.id)
        )
        await adding.execute(
            insert(CartItem).values(cart_id=cart_id, movie_id=movies["Inception"])
        )
        deleting = asyncio.create_task(
            delete_movie(client, moderator_token, movies["Inception"])
        )
        await asyncio.sleep(0.5)
        assert not deleting.done()  # waits for the add to finish
        await adding.commit()

    response = await deleting

    assert response.status_code == 409
    assert response.json() == {"detail": IN_ONE_CART}
    assert await movie_exists(db_session, movies["Inception"])
    assert cart_names((await get_cart(client, other_token)).json()) == ["Inception"]
