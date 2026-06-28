"""Celery app for background tasks (mirrors the Kroolo/PriceIQ worker setup)."""
import utils.agno_patches  # noqa: F401  -- apply agno reasoning patch before any agent runs
from celery import Celery
from celery.schedules import crontab

from app.settings import settings

celery_app = Celery(
    "worker",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
)

# Explicitly import task modules so they register (more reliable than autodiscovery).
import tasks.analyst_tasks  # noqa: E402,F401
import tasks.capture_tasks  # noqa: E402,F401
import tasks.contacts_tasks  # noqa: E402,F401
import tasks.crm_tasks  # noqa: E402,F401
import tasks.mail_tasks  # noqa: E402,F401
import tasks.sam_radar_tasks  # noqa: E402,F401
import tasks.sharepoint_tasks  # noqa: E402,F401

celery_app.autodiscover_tasks(packages=["tasks"], force=True)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)

# Scheduled jobs (run by `celery -A app.worker beat`). SAM.gov refreshes ~once a
# day (~03:30 GMT) and has no webhooks, so a single daily poll is the right cadence.
# 11:00 UTC = a few hours after the refresh, so the day's notices are all present.
# Trigger manually for testing:  celery -A app.worker call sam_radar.daily_scan
celery_app.conf.beat_schedule = {
    "sam-radar-daily-scan": {
        "task": "sam_radar.daily_scan",
        "schedule": crontab(hour=11, minute=0),
    },
}
