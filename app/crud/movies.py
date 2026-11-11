from sqlalchemy import (
    ColumnElement,
    Table,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.carts import CartItem
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
from app.crud.pagination import page_of
from app.crud.search import contains
from app.schemas.movies import MovieCreate, MovieListQuery, MovieUpdate

# Link table, its column for the linked item, the request field with ids.
LINKS = (
    (movie_genres, "genre_id", "genre_ids"),
    (movie_stars, "star_id", "star_ids"),
    (movie_directors, "director_id", "director_ids"),
)
LIST_FIELDS = tuple(field for _, _, field in LINKS)

# Everything the movie response shows, loaded right away: lazy loading in
# async code fails with MissingGreenlet -> 500.
MOVIE_DETAILS = (
    joinedload(Movie.certification),
    selectinload(Movie.genres),
    selectinload(Movie.stars),
    selectinload(Movie.directors),
)


async def get_movie(db: AsyncSession, movie_id: int) -> Movie | None:
    return await db.scalar(
        select(Movie).where(Movie.id == movie_id).options(*MOVIE_DETAILS)
    )


# What a catalog card shows (see MovieListItem).
CATALOG_DETAILS = (joinedload(Movie.certification), selectinload(Movie.genres))
SORT_COLUMNS = {
    "year": Movie.year,
    "price": Movie.price,
    "imdb": Movie.imdb,
    "votes": Movie.votes,
}


def _catalog_filters(query: MovieListQuery) -> list[ColumnElement[bool]]:
    conditions = []
    if query.search:  # "" = no search
        text = query.search
        conditions.append(
            or_(
                contains(Movie.name, text),
                contains(Movie.description, text),
                Movie.stars.any(contains(Star.name, text)),
                Movie.directors.any(contains(Director.name, text)),
            )
        )
    if query.year_from is not None:
        conditions.append(Movie.year >= query.year_from)
    if query.year_to is not None:
        conditions.append(Movie.year <= query.year_to)
    if query.imdb_min is not None:
        conditions.append(Movie.imdb >= query.imdb_min)
    # any() = EXISTS: a JOIN would repeat a movie once per matching link.
    if query.genre_id is not None:
        conditions.append(Movie.genres.any(Genre.id == query.genre_id))
    if query.star_id is not None:
        conditions.append(Movie.stars.any(Star.id == query.star_id))
    return conditions


async def list_movies(
    db: AsyncSession, query: MovieListQuery
) -> tuple[list[Movie], int]:
    """One page of the catalog and the number of movies on all pages.

    Both queries use the same filters, so `total` always matches what the
    pages show. Equal values (three movies of 1994) are ordered by id:
    otherwise the DB may return them in a different order each time and a
    movie could show up on two pages or on none.
    """
    conditions = _catalog_filters(query)
    total = await db.scalar(select(func.count()).select_from(Movie).where(*conditions))
    column = SORT_COLUMNS[query.sort.removeprefix("-")]
    order = column.desc() if query.sort.startswith("-") else column.asc()
    movies = await db.scalars(
        page_of(
            select(Movie).where(*conditions).order_by(order, Movie.id), query.page
        ).options(*CATALOG_DETAILS)
    )
    return list(movies), total


async def _add_links(
    db: AsyncSession, movie_id: int, table: Table, column: str, ids: list[int]
) -> None:
    # Core insert, not `movie.genres = [...]`: the ORM would load the items
    # by id and silently drop unknown ones, the DB foreign key rejects them.
    if ids:
        await db.execute(
            insert(table), [{"movie_id": movie_id, column: id_} for id_ in ids]
        )


async def create_movie(db: AsyncSession, data: MovieCreate) -> int:
    """Nothing is checked beforehand: the DB decides (unique name + year +
    time, existing ids), so a parallel request can't slip past a check.
    A violation raises IntegrityError and nothing is saved.
    """
    movie_id = await db.scalar(
        insert(Movie)
        .values(**data.model_dump(exclude=set(LIST_FIELDS)))
        .returning(Movie.id)
    )
    for table, column, field in LINKS:
        await _add_links(db, movie_id, table, column, getattr(data, field))
    await db.commit()
    return movie_id


async def update_movie(db: AsyncSession, movie_id: int, data: MovieUpdate) -> bool:
    """Returns False if the movie does not exist.

    The movie row is locked first, so a parallel update of the same movie
    waits: two requests can't mix their genre lists, and an update after
    a parallel delete finds nothing. Only the id is selected, so the
    session holds no Movie object that could show the old data later.
    A sent list replaces the old one. Like create, the DB checks the ids
    and the unique name + year + time (IntegrityError, nothing saved).
    """
    locked_id = await db.scalar(
        select(Movie.id).where(Movie.id == movie_id).with_for_update()
    )
    if locked_id is None:
        return False
    changes = data.model_dump(exclude_unset=True, exclude=set(LIST_FIELDS))
    if changes:
        await db.execute(update(Movie).where(Movie.id == movie_id).values(**changes))
    for table, column, field in LINKS:
        if field in data.model_fields_set:
            await db.execute(delete(table).where(table.c.movie_id == movie_id))
            await _add_links(db, movie_id, table, column, getattr(data, field))
    await db.commit()
    return True


class MovieInCartsError(Exception):
    """The movie is in users' carts and deleting it was not confirmed."""

    def __init__(self, carts: int) -> None:
        super().__init__(f"Carts with the movie: {carts}.")
        self.carts = carts


async def delete_movie(db: AsyncSession, movie_id: int, force: bool = False) -> bool:
    """Returns False if the movie does not exist.

    Without `force`, a movie that is in users' carts is not deleted:
    MovieInCartsError tells in how many. With `force` it is deleted and
    disappears from those carts (CASCADE), like its genre/star/director links.

    The movie row is locked first. Adding a movie to a cart checks the
    foreign key, which waits for this lock, so no cart can get the movie
    between the count and the delete: the count is exact.
    """
    locked_id = await db.scalar(
        select(Movie.id).where(Movie.id == movie_id).with_for_update()
    )
    if locked_id is None:
        return False
    if not force:
        # One movie is in a cart at most once, so items = carts.
        carts = await db.scalar(
            select(func.count()).where(CartItem.movie_id == movie_id)
        )
        if carts:
            await db.rollback()  # release the lock, nothing was changed
            raise MovieInCartsError(carts)
    await db.execute(delete(Movie).where(Movie.id == movie_id))
    await db.commit()
    return True


async def find_missing_ids(
    db: AsyncSession, data: MovieCreate | MovieUpdate
) -> dict[str, list[int]]:
    """Which of the sent ids don't exist, e.g. {"genre_ids": [99]}.

    Called only after the DB has rejected the movie, to tell the
    moderator exactly what to fix. Fields not sent in an update are None.
    """
    certification_ids = (
        [data.certification_id] if data.certification_id is not None else []
    )
    checks = (
        ("certification_id", Certification, certification_ids),
        ("genre_ids", Genre, data.genre_ids),
        ("star_ids", Star, data.star_ids),
        ("director_ids", Director, data.director_ids),
    )
    missing = {}
    for field, model, ids in checks:
        if not ids:
            continue
        found = set(await db.scalars(select(model.id).where(model.id.in_(ids))))
        not_found = [id_ for id_ in ids if id_ not in found]
        if not_found:
            missing[field] = not_found
    return missing
