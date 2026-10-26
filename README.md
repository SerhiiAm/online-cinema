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

## Running tests

Tests need a running PostgreSQL. They create a separate `<POSTGRES_DB>_test`
database on every run, so your development data is never touched.

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
docker compose up -d db
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
```

On Windows keep `POSTGRES_HOST=127.0.0.1` in `.env`: with `localhost`
every database connection waits about 2 seconds for an IPv6 attempt first.

## API

### Accounts

| Method | Path | Description |
|---|---|---|
| POST | `/accounts/register` | Register a new inactive user and issue a 24-hour activation token |
| POST | `/accounts/activate` | Activate the account with the email and activation token |
| POST | `/accounts/resend-activation` | Issue a new activation token if the account is not active yet |

Passwords must be at least 8 characters long and contain an uppercase letter,
a lowercase letter, a digit and a special character.

Activation emails are not sent yet — this comes with the Celery + Mailpit
integration. Until then the token can be read from the database:

```bash
docker compose exec db psql -U cinema -d cinema \
  -c "SELECT u.email, t.token FROM activation_tokens t JOIN users u ON u.id = t.user_id;"
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
alembic/      database migrations
tests/        pytest tests (mirrors the app/ structure)
```

## Planned features

1. User registration and account activation — in progress (activation emails pending)
2. JWT authentication and roles
3. Password change and reset
4. Movies: catalog and moderator CRUD
5. Shopping cart
6. Orders
7. Payments (Stripe)
