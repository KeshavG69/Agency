"""Read what a solicitation requires, in the background: gather files -> read -> save."""
import logging
import re
from datetime import date

from app.worker import celery_app
from client.crm_store import get_crm_store
from client.requirements_store import get_requirements_store

logger = logging.getLogger(__name__)

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DAY_FORMS = (
    (r"\b([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})\b", (3, 1, 2)),  # November 20, 2026
    (r"\b(\d{1,2})(?:st|nd|rd|th)? ([A-Za-z]{3,9})\.?,? (\d{4})\b", (3, 2, 1)),  # 20 November 2026
    (r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", (3, 1, 2)),                            # 11/20/2026
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", (1, 2, 3)),                                # 2026-11-20
)


def due_day(text: str | None) -> str | None:
    """The one date in a due-date line, as YYYY-MM-DD. None when there's no clear date, or more
    than one ("extended from Nov 20 to Nov 24"): an empty deadline is safer than a wrong one."""
    found = set()
    for pattern, (y, m, d) in _DAY_FORMS:
        for match in re.finditer(pattern, text or ""):
            month = match.group(m)
            if month.isalpha():
                prefix = month[:3].lower()
                month = _MONTHS.index(prefix) + 1 if prefix in _MONTHS else 0
            try:
                found.add(date(int(match.group(y)), int(month), int(match.group(d))).isoformat())
            except ValueError:
                continue
    return found.pop() if len(found) == 1 else None


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
    }, source_text=files.text)
    _fill_deadline(opportunity_id, organization_id, outline)
    if outline.solicitation_type == "rfi":
        _start_rfi(opportunity_id, organization_id)
    return {"opportunity_id": opportunity_id, "status": "ready", "items": len(outline.items)}


def _fill_deadline(opportunity_id: str, organization_id: str, outline) -> None:
    """Give the opportunity the deadline the solicitation states, so the Pipeline shows it and
    Today can schedule back from it. A date the proof check couldn't confirm isn't used."""
    if "due" in outline.submission.unverified:
        return
    day = due_day(outline.submission.due)
    if day and get_crm_store().set_read_deadline(opportunity_id, organization_id, day):
        from tasks.action_plan_tasks import plan_org_actions

        plan_org_actions.delay(organization_id)


def _start_rfi(opportunity_id: str, organization_id: str) -> None:
    """An RFI is meant to take minutes and nothing goes out without a person sending it, so it
    skips the list approval and goes straight to writing."""
    from tasks.action_plan_tasks import plan_org_actions
    from tasks.proposal_tasks import queue_package

    get_requirements_store().approve(organization_id, opportunity_id, "Collecct (RFI)")
    plan_org_actions.delay(organization_id)
    queue_package(opportunity_id, organization_id)


def queue_read(opportunity_id: str, organization_id: str, only_if_new: bool = True) -> bool:
    """Queue a reading. `only_if_new` keeps an automatic trigger (a Bid decision, an upload) from
    repeating work or overwriting a list someone is already working through."""
    if only_if_new and get_requirements_store().get(organization_id, opportunity_id):
        return False
    get_requirements_store().mark_reading(organization_id, opportunity_id)
    read_requirements_task.delay(opportunity_id, organization_id)
    return True
