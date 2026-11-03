import asyncio
from collections.abc import Awaitable, Callable

from celery.utils.log import get_task_logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.celery_app import celery_app
from app.core.config import settings
from app.crud import accounts as crud

logger = get_task_logger(__name__)


async def _run_with_db(func: Callable[[AsyncSession], Awaitable[int]]) -> int:
    # asyncio.run() starts a new event loop on every call and pooled asyncpg
    # connections can't move between loops, so use a fresh engine without a pool.
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as db:
            return await func(db)
    finally:
        await engine.dispose()


@celery_app.task
def delete_expired_activation_tokens() -> int:
    deleted = asyncio.run(_run_with_db(crud.delete_expired_activation_tokens))
    logger.info("Deleted %d expired activation tokens", deleted)
    return deleted


@celery_app.task
def delete_expired_password_reset_tokens() -> int:
    deleted = asyncio.run(_run_with_db(crud.delete_expired_password_reset_tokens))
    logger.info("Deleted %d expired password reset tokens", deleted)
    return deleted


@celery_app.task
def delete_expired_refresh_tokens() -> int:
    deleted = asyncio.run(_run_with_db(crud.delete_expired_refresh_tokens))
    logger.info("Deleted %d expired refresh tokens", deleted)
    return deleted
