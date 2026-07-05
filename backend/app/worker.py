"""Celery app for background tasks (mirrors the Kroolo/PriceIQ worker setup)."""
import utils.agno_patches  # noqa: F401  -- apply agno reasoning patch before any agent runs
from celery import Celery
from celery.schedules import crontab

from app.settings import settings

celery_app = Celery(
    "worker",
    broker=f"{settings.redis_base_url}/0",
    backend=f"{settings.redis_base_url}/1",
)

# Explicitly import task modules so they register (more reliable than autodiscovery).
import tasks.analyst_tasks  # noqa: E402,F401
import tasks.capture_tasks  # noqa: E402,F401
import tasks.contacts_tasks  # noqa: E402,F401
import tasks.crm_tasks  # noqa: E402,F401
import tasks.mail_tasks  # noqa: E402,F401
import tasks.notify_tasks  # noqa: E402,F401
import tasks.resync_tasks  # noqa: E402,F401
import tasks.sam_radar_tasks  # noqa: E402,F401
import tasks.sharepoint_tasks  # noqa: E402,F401

celery_app.autodiscover_tasks(packages=["tasks"], force=True)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # --- connection resilience -------------------------------------------------
    # Railway's Redis proxy drops idle TCP sockets after ~15 min, which surfaces as
    # "Connection reset by peer" tracebacks in the worker. A periodic health-check
    # ping keeps the idle socket warm (and detects a drop proactively); keepalive +
    # retry_on_timeout ride out transient blips; retry-on-startup avoids a boot race;
    # and cancel-long-running-tasks makes an in-flight task safe (re-queued cleanly)
    # if a drop does happen mid-run — e.g. the ~217 MB SAM.gov bulk download.
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "socket_keepalive": True,
        "health_check_interval": 30,
        "retry_on_timeout": True,
    },
    result_backend_transport_options={
        "socket_keepalive": True,
        "health_check_interval": 30,
        "retry_on_timeout": True,
    },
    redis_socket_keepalive=True,
    redis_retry_on_timeout=True,
    redis_backend_health_check_interval=30,
    worker_cancel_long_running_tasks_on_connection_loss=True,
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
    # Refresh SharePoint structure once a day (contacts are user-refreshed, not auto-synced).
    "daily-resync": {
        "task": "resync.daily",
        "schedule": crontab(hour=8, minute=0),
    },
}
