from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "online_cinema",
    broker=settings.redis_url,
    include=["app.tasks.email"],
)

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
)
