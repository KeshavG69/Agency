"""Write the bid package: one background task per document, so one failure can't lose the rest."""
import logging
import os
import re

from app.worker import celery_app
from client.crm_store import get_crm_store
from client.requirements_store import get_requirements_store

logger = logging.getLogger(__name__)

QUEUED, WRITING, WRITTEN, FAILED = "queued", "writing", "written", "failed"


def writable(row: dict) -> list[dict]:
    return [i for i in row.get("items") or [] if i.get("production", "write") == "write"]


def _filename(title: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9 ._()-]+", " ", title)
    return (" ".join(clean.split())[:80] or "document") + ".docx"


def free_name(row: dict, item_id: str, name: str) -> str:
    """SharePoint replaces a same-named file, so two checklist lines must never share a name."""
    used = {f["title"] for i in row.get("items") or [] if i["id"] != item_id
            for f in i.get("files") or []}
    base, ext = os.path.splitext(name)
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{base} ({n}){ext}", n + 1
    return candidate


def queue_package(opportunity_id: str, organization_id: str, employee_email: str | None = None,
                  only_unwritten: bool = True) -> int:
    """Queue every document Collecct can write. Returns how many."""
    store = get_requirements_store()
    row = store.get(organization_id, opportunity_id) or {}
    queued = 0
    for item in writable(row):
        if only_unwritten and item.get("gen_status") == WRITTEN:
            continue
        queue_item(opportunity_id, organization_id, item["id"], employee_email)
        queued += 1
    return queued


def queue_item(opportunity_id: str, organization_id: str, item_id: str,
               employee_email: str | None = None) -> None:
    get_requirements_store().edit_item(organization_id, opportunity_id, item_id,
                                       {"gen_status": QUEUED, "gen_error": None})
    write_item_task.delay(opportunity_id, organization_id, item_id, employee_email)


@celery_app.task(bind=True, name="proposal.write_item", max_retries=1, default_retry_delay=20)
def write_item_task(self, opportunity_id: str, organization_id: str, item_id: str,
                    employee_email: str | None = None) -> dict:
    from agent.proposal_writer import _page_budget, document_text, unverified_numbers, write_document
    from utils import bid_files
    from utils.docx_writer import render

    store, crm = get_requirements_store(), get_crm_store()
    row = store.get(organization_id, opportunity_id)
    item = next((i for i in (row or {}).get("items") or [] if i["id"] == item_id), None)
    opp = crm.get_opportunity(opportunity_id, organization_id)
    if item is None or opp is None:
        return {"item_id": item_id, "skipped": "not found"}

    store.edit_item(organization_id, opportunity_id, item_id, {"gen_status": WRITING})
    try:
        bid_files.bid_folder(organization_id, opportunity_id)  # before spending a model call
        source = store.source_text(organization_id, opportunity_id)
        doc = write_document(organization_id, opp, row, item, source, employee_email)
        unverified = unverified_numbers(doc, organization_id, source)
        gaps = list(doc.gaps)
        if unverified:
            gaps.append("Check these figures, which aren't in your documents: " + ", ".join(unverified))
        content = render(doc, opp.get("solicitation_number"), row.get("format_rules"))

        name = free_name(row, item_id, _filename(item.get("title") or doc.title))
        saved = bid_files.save(organization_id, opportunity_id, name, content,
                               bid_files.subfolder_for(item.get("title")))
        # One current draft per item. What a person attached stays alongside it.
        files = item.get("files") or []
        kept = [f for f in files if not f.get("generated")]
        previous = next((f["id"] for f in files if f.get("generated")), None)
        doc_id = bid_files.record(opportunity_id, "proposal_writer", "proposal", name, saved, previous)
        words = len(document_text(doc).split())
        limit, _ = _page_budget(item.get("page_limit"))
        pages = max(1, round(words / 500))
        if limit and pages > limit:
            gaps.append(f"About {pages} pages against a {limit}-page limit — needs trimming.")
        store.edit_item(organization_id, opportunity_id, item_id, {
            "files": [*kept, {"id": doc_id, "title": name, "size": len(content), "generated": True,
                              "edit_url": saved["edit_url"]}],
            "gen_status": WRITTEN, "gen_error": None, "gaps": gaps, "pages": pages,
        })
        logger.info("Proposal: wrote %s (%d words, %d gap(s))", name, words, len(gaps))
        return {"item_id": item_id, "status": WRITTEN, "gaps": len(gaps)}
    except bid_files.NoSharePoint as exc:  # a retry can't fix this; a person has to
        store.edit_item(organization_id, opportunity_id, item_id,
                        {"gen_status": FAILED, "gen_error": str(exc)})
        return {"item_id": item_id, "status": FAILED}
    except Exception as exc:  # noqa: BLE001
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        logger.warning("Proposal: couldn't write %s: %s", item.get("title"), exc)
        store.edit_item(organization_id, opportunity_id, item_id,
                        {"gen_status": FAILED, "gen_error": str(exc)[:300]})
        return {"item_id": item_id, "status": FAILED}
