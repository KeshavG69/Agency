"""Attach SAM.gov solicitation documents to opportunities — and re-judge with them.

Three entry points:

  attach_documents(opp_id, org_id)        the unit of work, a plain function (no Celery)
  sam_documents.ingest_then_analyze       used by daily ingestion: documents FIRST, then the
                                          Analyst, in one task, so a new notice is judged once —
                                          on its PWS, not its summary
  sam_documents.backfill                  the open opportunities ingested before this existed

WHY DOCUMENTS MUST COME BEFORE THE ANALYST. Ingestion used to upsert new notices and fire the
Analyst immediately. Bolting document-fetch on as a separate async job would race it: the
Analyst would usually finish first, judge from the summary, and need a second paid run once the
document landed. Doing both in one task, in order, judges each new notice once, with the
document. The Analyst runs in a `finally`, so a document failure can never cost a notice its
verdict.

WHY RE-JUDGE. A verdict is only as good as what the Analyst read. When a notice gains documents
it did not have — a backfill, or an amendment that adds or replaces files — its existing verdict
was formed without them. Those opportunities are re-analysed; ones whose files have not changed
are not (that is what `documents_fingerprint` is for), so re-running costs nothing.

Fail-soft throughout: one notice that cannot be read keeps its summary and the run continues.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.worker import celery_app
from client.crm_store import get_crm_store

logger = logging.getLogger(__name__)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def attach_documents(opportunity_id: str, organization_id: str, *, force: bool = False) -> dict:
    """Pull one opportunity's SAM.gov documents onto its record.

    Returns a status dict; `changed` is True only when new document text was stored — the
    signal the callers use to decide whether a verdict is now stale. Never raises.
    """
    from utils.sam_documents import fetch_solicitation_documents, fingerprint, list_attachments

    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, organization_id)
    if not opp:
        return {"status": "not_found", "changed": False}
    if opp.get("source") != "sam.gov" or not opp.get("notice_id"):
        # Manual uploads already carry their own documents; never overwrite those.
        return {"status": "not_sam", "changed": False}

    raw = list_attachments(opp["notice_id"])
    if raw is None:
        # Transient: record NOTHING, so the next run asks again instead of concluding the
        # notice has no documents.
        return {"status": "listing_failed", "changed": False}

    current = fingerprint(raw)
    if not force and opp.get("documents_fetched_at") and current == (opp.get("documents_fingerprint") or ""):
        return {"status": "unchanged", "changed": False}

    docs = fetch_solicitation_documents(opp["notice_id"], raw=raw)
    changed = False
    if docs.text:
        changed = crm.set_document_text(
            opportunity_id, organization_id, docs.text, document_url=docs.document_url,
        )
    crm.record_document_fetch(
        opportunity_id, organization_id, fingerprint=docs.fingerprint,
        attachments=docs.attachments, parsed=docs.parsed, error=docs.error,
    )
    logger.info("SAM documents %s (%s): parsed %d file(s) -> %d chars%s",
                opportunity_id, opp["notice_id"], docs.parsed, len(docs.text),
                f" [{docs.error}]" if docs.error else "")
    return {"status": "attached" if changed else (docs.error or "no_text"),
            "changed": changed, "parsed": docs.parsed, "chars": len(docs.text)}


def _reanalyze(opportunity_id: str, organization_id: str) -> bool:
    """Queue a fresh Analyst run for an already-judged, still-open opportunity whose documents
    just changed. Returns True when one was queued."""
    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, organization_id)
    if not opp or not opp.get("analyzed_at"):
        return False  # never judged: the batch's unanalyzed sweep will pick it up
    deadline = str(opp.get("response_deadline") or "")[:10]
    if deadline and deadline < _today():
        return False  # closed — a new verdict changes nothing a rep can act on
    from tasks.analyst_tasks import analyze_opportunity_task  # lazy: avoid task import cycle

    analyze_opportunity_task.delay(opp)
    return True


# Polite to a public .gov service: each opportunity is one listing call plus a few downloads.
@celery_app.task(name="sam_documents.for_opportunity", rate_limit="20/m",
                 max_retries=0, ignore_result=True)
def fetch_for_opportunity(opportunity_id: str, organization_id: str,
                          reanalyze: bool = True, force: bool = False) -> dict:
    """One opportunity: attach its documents, and re-judge it if they changed."""
    res = attach_documents(opportunity_id, organization_id, force=force)
    if reanalyze and res.get("changed"):
        res["reanalyzed"] = _reanalyze(opportunity_id, organization_id)
    return res


@celery_app.task(name="sam_documents.ingest_then_analyze", max_retries=0)
def ingest_then_analyze(organization_id: str, opportunity_ids: list[str],
                        analyze: bool = True) -> dict:
    """Ingestion's second half: documents for the notices just upserted, THEN the Analyst.

    Sequential on purpose — one org's fresh notices, a few downloads each, against a public
    service. The Analyst batch runs in `finally`: a document failure must never cost a notice
    its verdict.
    """
    tally = {"attached": 0, "unchanged": 0, "no_documents": 0, "failed": 0, "reanalyzed": 0}
    try:
        for oid in opportunity_ids or []:
            try:
                res = attach_documents(oid, organization_id)
            except Exception as exc:  # noqa: BLE001 — one notice never sinks the run
                logger.warning("SAM documents failed for %s: %s", oid, exc)
                tally["failed"] += 1
                continue
            status = res.get("status")
            if res.get("changed"):
                tally["attached"] += 1
                # An UPDATED notice (amendment) already had a verdict; it is now stale.
                if analyze and _reanalyze(oid, organization_id):
                    tally["reanalyzed"] += 1
            elif status == "unchanged":
                tally["unchanged"] += 1
            elif status == "listing_failed":
                tally["failed"] += 1
            else:
                tally["no_documents"] += 1
    finally:
        if analyze:
            from tasks.analyst_tasks import run_analyst_batch  # lazy: avoid task import cycle

            # Judges every still-unanalyzed opportunity — the new ones, now WITH documents.
            run_analyst_batch.delay(organization_id)
    logger.info("SAM documents for org %s: %s", organization_id, tally)
    return {"organization_id": organization_id, **tally}


@celery_app.task(name="sam_documents.backfill", max_retries=0)
def backfill(organization_id: str | None = None, limit: int | None = None,
             reanalyze: bool = True) -> dict:
    """Queue document fetches for OPEN SAM.gov opportunities that have never been fetched.

    Ordered by what a rep would act on first — Bid, then Watch, then No-Bid, then unread —
    and by priority within each, so a partial backfill still covers what matters. Closed
    notices are skipped: documents cannot change a decision that is already moot.
    """
    crm = get_crm_store()
    q: dict = {
        "source": "sam.gov",
        "notice_id": {"$type": "string", "$ne": ""},
        "response_deadline": {"$gte": _today()},
        "documents_fetched_at": {"$exists": False},
    }
    if organization_id:
        q["organization_id"] = organization_id
    rank = {"Bid": 0, "Watch": 1, "No-Bid": 2}
    rows = list(crm.opps.find(q, {"organization_id": 1, "bid_decision": 1, "priority_score": 1}))
    rows.sort(key=lambda r: (rank.get(r.get("bid_decision"), 3), -(r.get("priority_score") or 0)))
    if limit:
        rows = rows[: int(limit)]
    for r in rows:
        fetch_for_opportunity.delay(str(r["_id"]), str(r["organization_id"]), reanalyze=reanalyze)
    logger.info("SAM documents backfill: queued %d open opportunities", len(rows))
    return {"queued": len(rows)}
