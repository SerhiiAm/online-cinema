import asyncio
from collections.abc import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.accounts import UserGroup, UserGroupEnum

TEST_DATABASE_URL = make_url(settings.database_url).set(
    database=f"{settings.postgres_db}_test"
)


async def _create_test_database() -> None:
    admin_engine = create_async_engine(
        TEST_DATABASE_URL.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    async with admin_engine.connect() as connection:
        name = TEST_DATABASE_URL.database
        await connection.execute(
            text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        )
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    await admin_engine.dispose()

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine)() as session:
        session.add_all([UserGroup(name=group) for group in UserGroupEnum])
        await session.commit()
    await engine.dispose()


@pytest.fixture(scope="session")
def test_database() -> None:
    asyncio.run(_create_test_database())


@pytest.fixture
async def db_engine(test_database: None) -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield engine
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE users, activation_tokens RESTART IDENTITY")
        )
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def send_activation_email_mock() -> Generator[MagicMock, None, None]:
    # Replaces .delay so tests never need a running Redis.
    with patch("app.routers.accounts.send_activation_email.delay") as mock:
        yield mock


@pytest.fixture
async def client(
    db_engine: AsyncEngine, send_activation_email_mock: MagicMock
) -> AsyncGenerator[AsyncClient, None]:
    session_maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
