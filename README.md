# Online Cinema

Async REST API for an online cinema: movie catalog, shopping cart, orders and Stripe payments.

Built with FastAPI, PostgreSQL, SQLAlchemy 2.0 (async), Alembic, Celery, Docker.

## Quick start

```bash
git clone https://github.com/SerhiiAm/online-cinema.git
cd online-cinema
cp .env.example .env
docker compose up --build
```

- API docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Running tests

```bash
docker compose exec app pytest -v
```

## Local development (without Docker for the app)

```bash
poetry install
docker compose up -d db
alembic upgrade head
uvicorn app.main:app --reload
```

## Project structure

```
app/        application code
alembic/    database migrations
tests/      pytest tests
```

## Planned features

1. User registration and account activation
2. JWT authentication and roles
3. Password change and reset
4. Movies: catalog and moderator CRUD
5. Shopping cart
6. Orders
7. Payments (Stripe)
