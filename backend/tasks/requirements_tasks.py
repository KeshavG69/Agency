"""Read what a solicitation requires, in the background: gather files -> read -> save."""
import logging

from app.worker import celery_app
from client.crm_store import get_crm_store
from client.requirements_store import get_requirements_store

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="requirements.read", max_retries=2, default_retry_delay=30)
def read_requirements_task(self, opportunity_id: str, organization_id: str) -> dict:
    from agent.requirements_agent import read_requirements
    from utils.solicitation_files import gather_files

    store = get_requirements_store()
    opp = get_crm_store().get_opportunity(opportunity_id, organization_id)
    if opp is None:
        return {"opportunity_id": opportunity_id, "skipped": "not found"}
    store.mark_reading(organization_id, opportunity_id)
    try:
        files = gather_files({**opp, "id": opportunity_id})
        if not files.text.strip():
            store.mark_failed(organization_id, opportunity_id,
                              "There is no readable text in this opportunity's files.")
            return {"opportunity_id": opportunity_id, "status": "failed"}
        outline = read_requirements(opp.get("title") or "this solicitation", files.text)
    except Exception as exc:  # noqa: BLE001 — a dropped connection is worth a retry
        if self.request.retries < self.max_retries:
            logger.warning("Requirements read failed for %s, retrying: %s", opportunity_id, exc)
            raise self.retry(exc=exc)
        store.mark_failed(organization_id, opportunity_id, f"Couldn't read the requirements: {exc}")
        return {"opportunity_id": opportunity_id, "status": "failed"}
    store.save_outline(organization_id, opportunity_id, outline, {
        "read": files.read, "not_read": files.not_read,
        "from_summary": files.from_summary, "truncated": files.truncated,
    })
    return {"opportunity_id": opportunity_id, "status": "ready", "items": len(outline.items)}


def queue_read(opportunity_id: str, organization_id: str, only_if_new: bool = True) -> bool:
    """Queue a reading. `only_if_new` keeps an automatic trigger (a Bid decision, an upload) from
    repeating work or overwriting a list someone is already working through."""
    if only_if_new and get_requirements_store().get(organization_id, opportunity_id):
        return False
    get_requirements_store().mark_reading(organization_id, opportunity_id)
    read_requirements_task.delay(opportunity_id, organization_id)
    return True
