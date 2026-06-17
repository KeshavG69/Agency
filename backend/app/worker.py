"""Celery app for background tasks (mirrors the Kroolo/PriceIQ worker setup)."""
import utils.agno_patches  # noqa: F401  -- apply agno reasoning patch before any agent runs
from celery import Celery

from app.settings import settings

celery_app = Celery(
    "worker",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
)

# Explicitly import task modules so they register (more reliable than autodiscovery).
import tasks.analyst_tasks  # noqa: E402,F401
import tasks.capture_tasks  # noqa: E402,F401

celery_app.autodiscover_tasks(packages=["tasks"], force=True)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
