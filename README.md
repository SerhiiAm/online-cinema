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

| Method | Path | Description |
|---|---|---|
| POST | `/accounts/register` | Register a new inactive user and send an activation email (token valid for 24 hours) |
| POST | `/accounts/activate` | Activate the account with the email and activation token |
| POST | `/accounts/resend-activation` | Send a new activation email if the account is not active yet |

Passwords must be at least 8 characters long and contain an uppercase letter,
a lowercase letter, a digit and a special character.

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

## Project structure

```
app/
  core/       settings, database, security and validators
  models/     SQLAlchemy models
  schemas/    Pydantic request and response schemas
  crud/       database operations
  routers/    API endpoints
  tasks/      Celery tasks
alembic/      database migrations
tests/        pytest tests (mirrors the app/ structure)
```

## Planned features

1. User registration and account activation — done
2. JWT authentication and roles
3. Password change and reset
4. Movies: catalog and moderator CRUD
5. Shopping cart
6. Orders
7. Payments (Stripe)
