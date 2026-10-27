from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "online_cinema",
    broker=settings.redis_url,
    include=["app.tasks.email"],
)

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    # Default is 3 retries: an API request would hang ~20 s if Redis is down.
    task_publish_retry_policy={"max_retries": 1},
)
