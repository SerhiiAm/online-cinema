# Online Cinema

Async REST API for an online cinema: movie catalog, shopping cart, orders and Stripe payments.

Built with FastAPI, PostgreSQL, SQLAlchemy 2.0 (async), Alembic, Celery, Docker.

## Quick start

```bash
git clone https://github.com/SerhiiAm/online-cinema.git
cd online-cinema
cp .env.example .env
docker compose up --build -d
docker compose exec app alembic upgrade head
```

Before the first start, set `JWT_SECRET_KEY` in `.env` to a long random string, e.g.
the output of `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

- API docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/health
- Mailpit (local inbox for outgoing emails): http://localhost:8025

## Services

| Service | Purpose |
|---|---|
| `app` | FastAPI application |
| `db` | PostgreSQL |
| `redis` | Message broker for Celery |
| `celery_worker` | Runs background tasks (sending emails, deleting expired tokens) |
| `celery_beat` | Puts periodic tasks into the queue on schedule |
| `mailpit` | Fake SMTP server that catches all outgoing emails locally |

Worker and beat logs:

```bash
docker compose logs -f celery_worker celery_beat
```

Unlike `app`, the worker and beat do not reload on code changes. Restart them
after editing tasks or the schedule:

```bash
docker compose restart celery_worker celery_beat
```

## Running tests

Tests need a running PostgreSQL. They create a separate `<POSTGRES_DB>_test`
database on every run, so your development data is never touched.
Redis is not needed: sending Celery tasks is mocked.

```bash
# inside Docker
docker compose exec app pytest -v

# or locally
docker compose up -d db
poetry run pytest -v
```

With a coverage report:

```bash
poetry run pytest --cov=app --cov-report=term-missing
```

## Local development (without Docker for the app)

```bash
poetry install
docker compose up -d db redis mailpit
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload

# in a second terminal
poetry run celery -A app.core.celery_app worker --loglevel=info --pool=solo

# in a third terminal (only needed for periodic tasks)
poetry run celery -A app.core.celery_app beat --loglevel=info
```

`--pool=solo` is needed on Windows, where Celery's default pool does not work.

On Windows keep `POSTGRES_HOST=127.0.0.1` in `.env`: with `localhost`
every database connection waits about 2 seconds for an IPv6 attempt first.

## API

### Accounts

| Method | Path | Access | Description |
|---|---|---|---|
| POST | `/accounts/register` | public | Register a new inactive user and send an activation email (token valid for 24 hours) |
| POST | `/accounts/activate` | public | Activate the account with the email and activation token |
| POST | `/accounts/resend-activation` | public | Send a new activation email if the account is not active yet |
| POST | `/accounts/login` | public | Get an access + refresh token pair |
| POST | `/accounts/refresh` | public | Exchange a refresh token for a new pair |
| POST | `/accounts/logout` | public | Revoke the given refresh token (this session only) |
| GET | `/accounts/me` | any user | Get the current user and their group |
| POST | `/accounts/change-password` | any user | Change the password using the current one |
| POST | `/accounts/password-reset/request` | public | Send a password reset email if the account is active (token valid for 60 minutes) |
| POST | `/accounts/password-reset/confirm` | public | Set a new password with the email and reset token, without the old password |
| PATCH | `/accounts/users/{user_id}/group` | admin | Move a user between `user` and `moderator` |
| POST | `/accounts/users/{user_id}/activate` | admin | Activate an account manually, without the email token |

`refresh` and `logout` are public because they are authorized by the refresh
token in the body, not by the `Authorization` header.

Passwords must be at least 8 characters long and contain an uppercase letter,
a lowercase letter, a digit and a special character. The same rules apply to
a new password set via change or reset.

To activate an account locally:

1. Register via `POST /accounts/register` in Swagger.
2. Open Mailpit at http://localhost:8025 and find the activation email.
3. Copy the token from the email into `POST /accounts/activate`.

The link in the email points to a frontend page (`FRONTEND_URL`), which is not
part of this project, so use the token directly.

Expired activation tokens are deleted every hour (at :00) by `celery_beat`.
The user account stays, so the user can still request a new email via
`/accounts/resend-activation`. To run the cleanup right away:

```bash
docker compose exec app celery -A app.core.celery_app call app.tasks.accounts.delete_expired_activation_tokens
docker compose logs celery_worker | grep "expired activation tokens"
```

Full request and response schemas are available in Swagger.

### Authentication

1. `POST /accounts/login` with the email and password of an active account.
2. Copy the `access_token` from the response.
3. In Swagger click **Authorize** and paste it. Protected endpoints now send
   `Authorization: Bearer <token>`.

| Token | Lifetime | Used for |
|---|---|---|
| access | 15 minutes | every protected request |
| refresh | 7 days | getting a new pair via `/accounts/refresh` |

A refresh token works only once: `/accounts/refresh` revokes it and returns a
new one. If an already used refresh token is sent again, it is treated as
stolen and **all** sessions of the user are revoked, so they must log in again.

The user and their group are loaded from the database on every request, so a
group change or a deactivation applies immediately, even with an old token.

Expired refresh tokens are deleted once a day at 03:00 UTC by `celery_beat`.
Revoked tokens that have not expired yet are kept on purpose: they are needed
to detect the reuse of a stolen token. To run the cleanup right away:

```bash
docker compose exec app celery -A app.core.celery_app call app.tasks.accounts.delete_expired_refresh_tokens
```

### Password change and reset

**Change** (the user remembers the password): log in, click **Authorize**, then
call `POST /accounts/change-password` with the current and the new password.
The new password must differ from the current one.

**Reset** (the password is forgotten), locally:

1. `POST /accounts/password-reset/request` with the email.
2. Open Mailpit at http://localhost:8025 and find the "Reset your password" email.
3. Copy the token from the email into `POST /accounts/password-reset/confirm`
   together with the email and the new password.

The reset token is valid for 60 minutes and works only once. A new request
invalidates the previous email. The response to a request is the same whether
the email is registered or not, so it can't be used to find out who has an
account. Accounts that are not activated yet get no reset email.

After a successful change or reset **all** sessions of the user are logged out
(every refresh token is revoked), so a stolen token stops working. A pending
reset link is also invalidated by a password change.

Expired reset tokens are deleted every hour (at :30) by `celery_beat`. To run
the cleanup right away:

```bash
docker compose exec app celery -A app.core.celery_app call app.tasks.accounts.delete_expired_password_reset_tokens
```

### Roles

| Group | Permissions |
|---|---|
| `user` | default group of every new account |
| `moderator` | everything a user can, plus managing movies, genres, stars, directors and certifications |
| `admin` | everything a moderator can, plus changing groups, activating accounts and viewing users' carts |

Each group inherits the permissions of the groups below it.

The `admin` group is managed only on the server, not via the API. To create the
first admin, register an account as usual and then run:

```bash
docker compose exec app python -m app.scripts.make_admin admin@example.com
```

The script makes the existing account an admin and activates it, so the
activation email is not needed. Via the API an admin can only move users
between `user` and `moderator`; admin accounts, including their own, can't be
changed there. This way no admin can demote another one, and the system can
never end up without an admin because of two admins demoting each other.

### Movies

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/movies` | public | Browse the catalog page by page, with search, filters and sorting |
| POST | `/movies` | moderator | Create a movie |
| GET | `/movies/{movie_id}` | public | Get a movie with its certification, genres, stars and directors |
| PATCH | `/movies/{movie_id}` | moderator | Change only the sent fields of a movie |
| DELETE | `/movies/{movie_id}` | moderator | Delete a movie (its genres, stars and directors stay); asks for `force=true` if it is in users' carts |

"moderator" here means a moderator or an admin.

**Catalog.** `GET /movies` returns 10 movies per page as
`{"items": [...], "total": 42, "page": 1}`, where `total` counts the movies on
all pages. A page after the last one is empty, not an error. The list shows a
short card of each movie; the full data is at `GET /movies/{movie_id}`.

| Query parameter | Meaning |
|---|---|
| `page` | Page number, starting from 1 |
| `search` | Text in the title, description, a star's or a director's name |
| `year_from`, `year_to` | Release years, both included |
| `imdb_min` | Minimum IMDb rating, included |
| `genre_id`, `star_id` | Only movies with this genre / star |
| `sort` | `year`, `price`, `imdb` or `votes` (popularity); `-` in front means descending. Default `-year` (newest first) |

All parameters can be combined. Search ignores case and looks for the text as a
whole: `dark knight` finds "The Dark Knight", `knight dark` does not. An empty
search shows all movies. An unknown `genre_id` or `star_id` gives an empty
page, not a 404.

**Creating and updating.** The certification, genres, stars and directors are
passed by id (`certification_id`, `genre_ids`, `star_ids`, `director_ids`), so
create them first in their own sections. Rules for a movie:

- at least one genre and one director are required, stars are optional;
- at most 50 ids in each list, repeated ids are counted once;
- an unknown id gives 400 listing the missing ids;
- the year is from 1888 to the current year + 5;
- the price is above 0 with at most 2 decimal places;
- the same name, year and duration can't be used twice (409).

`PATCH` changes only the sent fields, with the same rules. A sent list replaces
the old one: `"star_ids": []` removes all stars. Only `meta_score` and `gross`
can be cleared with `null`. Note that Swagger's example body for `PATCH`
contains every field, so sending it unchanged overwrites the whole movie:
delete the fields you don't want to change.

**Deleting.** If the movie is in users' carts, `DELETE /movies/{movie_id}`
deletes nothing and answers 409 with the number of those carts. To delete it
anyway, repeat the request as `DELETE /movies/{movie_id}?force=true`: the movie
is also removed from those carts. A movie that is in no cart is deleted right
away, `force` is not needed.

### Genres, stars, directors and certifications

All four have the same endpoints (`{item}` is `genres`, `stars`, `directors`
or `certifications`):

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/{item}` | public | List sorted by name, each with the number of its movies |
| POST | `/{item}` | moderator | Create one with the given name |
| PATCH | `/{item}/{item_id}` | moderator | Rename it; movies show the new name at once |
| DELETE | `/{item}/{item_id}` | moderator | Delete it if no movie uses it |

- `/genres` and `/certifications` return a plain list: there are only a few
  dozen of them.
- `/stars` and `/directors` return pages of 10 (`{"items", "total", "page"}`)
  and accept `search` by a part of the name, because a catalog has thousands
  of them.
- Names are unique ignoring case (`Drama` and `drama` are the same), spaces
  around a name are removed.
- Deleting one that movies still use gives 409 with the number of those
  movies: change the movies first. This way a mistaken delete can't silently
  strip a genre from hundreds of movies.

### Cart

Every user has one cart. It is created by itself on the first add.

| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/cart` | any user | View your cart: the movies and the total price |
| POST | `/cart/items` | any user | Add a movie: `{"movie_id": 1}`; returns the whole cart |
| DELETE | `/cart/items/{movie_id}` | any user | Remove one movie from your cart |
| DELETE | `/cart` | any user | Remove all movies from your cart |
| GET | `/cart/users/{user_id}` | admin | View the cart of any user |

"any user" means a logged-in user with an active account. Without a token
every cart endpoint answers 401: sign up and log in first.

The cart looks like this:

```json
{
  "items": [
    {
      "movie": {
        "id": 1,
        "name": "Inception",
        "year": 2010,
        "price": "19.99",
        "genres": [{"id": 3, "name": "Action"}]
      },
      "added_at": "2026-11-10T18:30:00Z"
    }
  ],
  "total": "19.99"
}
```

- Movies are listed in the order they were added; `total` is the sum of their
  current prices.
- A movie can be in a cart only once: adding it again gives 409. An unknown
  `movie_id` gives 404.
- Removing a movie that is not in your cart gives 404. Clearing an empty cart
  is not an error.
- `GET /cart/users/{user_id}` shows an empty cart for a user who has added
  nothing yet, and 404 for a user id that doesn't exist.
- When a moderator deletes a movie with `force=true`, it disappears from all
  carts (see "Deleting" in Movies).

### Design decisions

These deliberately differ from the original task:

- **Refresh tokens are stored by `jti`, not as the token itself.** The database
  keeps only the token's unique id, its expiry and a `revoked` flag. A database
  leak then gives an attacker no usable tokens, the same idea as storing
  password hashes instead of passwords.
- **Logout revokes the refresh token instead of deleting it.** A deleted token
  would look "unknown" if it showed up again, and its reuse could not be
  detected. Revoked tokens are deleted by the daily cleanup once they expire.
- **Access tokens are not revoked on logout.** They are not stored, so an
  access token stays valid for the rest of its lifetime (at most 15 minutes).
  The client deletes both tokens on logout; a Redis blacklist for access tokens
  was considered not worth the extra complexity for this window.
- **Password reset tokens are stored as a SHA-256 hash.** The `token` column
  holds the hash, and the token itself exists only in the email. A database
  leak then can't be used to reset anyone's password. A fast unsalted hash is
  enough here because the token is random, not a guessable password, and it
  lets the row be found by the hash.

## Project structure

```
app/
  core/       settings, database, security and validators
  models/     SQLAlchemy models
  schemas/    Pydantic request and response schemas
  crud/       database operations
  routers/    API endpoints
  tasks/      Celery tasks
  scripts/    server-side admin commands (make_admin)
alembic/      database migrations
tests/        pytest tests (mirrors the app/ structure)
```

## Planned features

1. User registration and account activation — done
2. JWT authentication and roles — done
3. Password change and reset — done
4. Movies: catalog and moderator CRUD — done
5. Shopping cart — done
6. Orders
7. Payments (Stripe)
