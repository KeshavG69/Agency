"""Build an opportunity's capability matrix in the background, and file it in SharePoint."""
import logging

from app.worker import celery_app
from client.capability_store import get_capability_store
from client.crm_store import get_crm_store
from client.requirements_store import get_requirements_store

logger = logging.getLogger(__name__)

FILE_NAME = "Capability Matrix.xlsx"
SUBFOLDER = "Capture Docs"


@celery_app.task(bind=True, name="capability.build", max_retries=1, default_retry_delay=30)
def build_capability_task(self, opportunity_id: str, organization_id: str,
                          employee_email: str | None = None) -> dict:
    from agent.capability_agent import build_matrix
    from agent.company_profile import company_card
    from client.graph_store import _embed_query

    store = get_capability_store()
    opp = get_crm_store().get_opportunity(opportunity_id, organization_id)
    if opp is None:
        return {"opportunity_id": opportunity_id, "skipped": "not found"}
    store.mark_building(organization_id, opportunity_id)

    source = get_requirements_store().source_text(organization_id, opportunity_id)
    if not source.strip():
        store.mark_failed(organization_id, opportunity_id,
                          "Open the Requirements tab first, so Collecct can read the solicitation.")
        return {"opportunity_id": opportunity_id, "status": "failed"}
    # Without library search every requirement would come back "nothing in the library" — a
    # matrix of false gaps that looks real. Say what's wrong instead.
    if _embed_query("capability probe") is None:
        store.mark_failed(organization_id, opportunity_id,
                          "Library search isn't available, so nothing can be scored. Check OPENAI_API_KEY.")
        return {"opportunity_id": opportunity_id, "status": "failed"}

    company = company_card(organization_id).get("name") or "the company"
    try:
        areas, contacts, stats = build_matrix(organization_id, opp, source, company, employee_email)
    except Exception as exc:  # noqa: BLE001
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        store.mark_failed(organization_id, opportunity_id, f"Couldn't build the matrix: {exc}")
        return {"opportunity_id": opportunity_id, "status": "failed"}
    if not areas:
        store.mark_failed(organization_id, opportunity_id,
                          "No work requirements were found in the solicitation files.")
        return {"opportunity_id": opportunity_id, "status": "failed"}

    matrix = store.save(organization_id, opportunity_id, [a.model_dump() for a in areas], contacts, stats)
    _file(opportunity_id, organization_id, opp, matrix, company)
    return {"opportunity_id": opportunity_id, "status": "ready", **stats}


def _file(opportunity_id: str, organization_id: str, opp: dict, matrix: dict, company: str) -> None:
    """The Excel copy in the bid's Capture Docs folder. The matrix is still usable in Collecct if
    SharePoint isn't, so a failure here is noted, not fatal."""
    from utils import bid_files
    from utils.capability_xlsx import workbook

    store = get_capability_store()
    try:
        content = workbook(matrix, company, opp.get("solicitation_number"), opp.get("title"))
        saved = bid_files.save(organization_id, opportunity_id, FILE_NAME, content, SUBFOLDER)
        previous = (matrix.get("file") or {}).get("document_id")
        doc_id = bid_files.record(opportunity_id, "capability", "capability_matrix", FILE_NAME, saved, previous)
        store.set_file(organization_id, opportunity_id,
                       {"document_id": doc_id, "title": FILE_NAME, "edit_url": saved.get("edit_url")})
    except bid_files.NoSharePoint as exc:
        store.set_file(organization_id, opportunity_id, matrix.get("file"), str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Capability: couldn't file the workbook for %s: %s", opportunity_id, exc)
        store.set_file(organization_id, opportunity_id, matrix.get("file"), f"Couldn't save the Excel file: {exc}"[:300])


def queue_build(opportunity_id: str, organization_id: str, employee_email: str | None = None) -> None:
    get_capability_store().mark_building(organization_id, opportunity_id)
    build_capability_task.delay(opportunity_id, organization_id, employee_email)
