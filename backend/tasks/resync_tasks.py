"""Daily resync — refresh each org's SharePoint structure every 24h (Celery beat).

Contacts are deliberately NOT auto-synced: each employee refreshes their own contacts
on demand from the Contacts page ("Refresh"), choosing which to import via the review
dialog. A blind daily pull would re-add contacts the user chose to skip, so it's removed."""
import logging

from app.worker import celery_app
from auth.database import get_mongodb_client
from utils.composio_utils import connection_status, sharepoint_entity

logger = logging.getLogger(__name__)


@celery_app.task(name="resync.daily")
def daily_resync() -> dict:
    """Re-crawl SharePoint structure for every org that has it connected. Connection is
    checked first so we never retry-storm unconnected accounts."""
    db = get_mongodb_client().get_database()
    from tasks.sharepoint_tasks import sync_sharepoint_structure_task

    sp = 0
    # SharePoint — per org, only if connected (the task also no-ops if not).
    for o in db["organizations"].find({}, {"_id": 1}):
        oid = str(o["_id"])
        try:
            if connection_status("sharepoint", sharepoint_entity(oid)).get("connected"):
                sync_sharepoint_structure_task.delay(oid)
                sp += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("daily_resync: sharepoint check failed for %s: %s", oid, exc)

    logger.info("daily_resync: dispatched sharepoint=%d", sp)
    return {"sharepoint_dispatched": sp}
