"""Celery tasks — read every library document, sort it, and pull out its facts.

Runs after each SharePoint structure sync (tasks/sharepoint_tasks.py), so the daily resync
keeps it current with no button to press. Only new or changed files are read: the sync marks
them `pending` in library_documents by comparing each file's content tag (cTag) with the one
it was last analysed at.

One task per document, so the worker's thread pool reads many at once and one bad file never
holds up the rest.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from datetime import datetime, timezone

from app.settings import settings
from app.worker import celery_app
from client.library_store import (
    DONE,
    FAILED,
    OUTPUT,
    PENDING,
    REVIEW,
    SOURCE_TEXT_CHARS,
    get_library_store,
)

logger = logging.getLogger(__name__)


def _parse_bytes(content: bytes, ext: str | None, name: str) -> str | None:
    """LiteParse wants a path whose extension names the format (see utils/doc_parse.py)."""
    from utils.doc_parse import parse_document

    fd, path = tempfile.mkstemp(suffix=f".{ext}" if ext else "")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        return parse_document(path, filename=name)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _item_embeddings(result) -> dict:
    """One vector per contract and per person, so a search ranks the right PERSON in a resume
    book (or the right contract in a past-performance volume) instead of ranking the file."""
    from client.graph_store import _embedder

    def text(obj, keys):
        return " | ".join(
            ", ".join(v) if isinstance(v, list) else str(v)
            for k in keys if (v := getattr(obj, k, None)) not in (None, "", [])
        )

    contracts = [text(c, ("title", "customer", "agency", "scope", "naics", "role")) for c in result.contracts]
    people = [text(p, ("name", "current_role", "skills", "certifications", "clearance", "education"))
              for p in result.people]
    texts = contracts + people
    if not texts or not settings.embeddings_enabled:
        return {}
    try:
        vecs = _embedder().embed_documents(texts)
    except Exception as exc:  # noqa: BLE001 — ranking falls back to the file's summary vector
        logger.warning("Library item embeddings failed: %s", exc)
        return {}
    return {"contracts": vecs[:len(contracts)], "people": vecs[len(contracts):]}


def _uploaded_bytes(row: dict) -> bytes:
    """A company document uploaded straight into Collecct lives in iDrive, not SharePoint."""
    from client.idrive_storage import get_idrive_storage

    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        get_idrive_storage().download_document(row["object_key"], path)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


@celery_app.task(name="library.analyse_org")
def analyse_library_task(organization_id: str) -> dict:
    """Queue one analysis per pending file in the org's library."""
    store = get_library_store()
    revived = store.requeue_stuck(organization_id)
    ids = store.pending_ids(organization_id)
    for iid in ids:
        analyse_document_task.delay(organization_id, iid)
    logger.info("Library: queued %d document(s) for org %s (%d revived from a stalled run)",
                len(ids), organization_id, revived)
    return {"queued": len(ids), "revived": revived}


@celery_app.task(bind=True, name="library.analyse_document", max_retries=2)
def analyse_document_task(self, organization_id: str, item_id: str) -> dict:
    """Download -> parse -> sort + extract (proof-gated) -> save to Mongo -> mirror to the graph."""
    from agent.library_agent import REVIEW_BELOW, analyse_document
    from client.graph_store import _embed_query
    from client.library_graph import summary_text, write_document
    from utils.file_store import download

    store = get_library_store()
    row = store.claim(organization_id, item_id)
    if row is None:
        return {"item_id": item_id, "skipped": "not pending (already taken or removed)"}
    name = row.get("name") or item_id
    try:
        content = (_uploaded_bytes(row) if row.get("source") == "upload"
                   else download(organization_id, row.get("drive_id"), item_id))
        text = _parse_bytes(content, row.get("ext"), name)
        if not text or len(text.strip()) < 40:
            store.mark(organization_id, item_id, status=FAILED,
                       error="No readable text in this file (it may be a scan or an empty template)",
                       analysed_version=row.get("version"))
            return {"item_id": item_id, "status": FAILED}

        result = analyse_document(name, row.get("path") or "", text,
                                  force_category=row.get("category_override"))
        status = DONE if (row.get("category_override") or result.confidence >= REVIEW_BELOW) else REVIEW
        fields = {
            **result.model_dump(),
            "status": status, "error": None, "chars": len(text),
            "analysed_version": row.get("version"),
            "source_text": text[:SOURCE_TEXT_CHARS],
        }
        if row.get("shelf") == OUTPUT:  # listed for people, never in the graph the agents search
            store.mark(organization_id, item_id, analysed_at=datetime.now(timezone.utc), **fields)
            return {"item_id": item_id, "status": status, "category": result.category}
        fields["summary_embedding"] = _embed_query(summary_text({**row, **fields}))
        fields["item_embeddings"] = _item_embeddings(result)
        fields["analysed_at"] = datetime.now(timezone.utc)
        # Until the graph write below succeeds, the passages exist nowhere: relink() can restore
        # properties and edges from Mongo, but passages need the document text. So the row is
        # flagged, and the next sync re-reads it if this write never lands.
        fields["graph_pending"] = True
        store.mark(organization_id, item_id, **fields)

        passages = 0
        for attempt in range(3):
            try:
                passages = write_document(organization_id, {**row, **fields, "item_id": item_id}, text)
                store.mark(organization_id, item_id, graph_pending=False)
                break
            except Exception as exc:  # noqa: BLE001 — FalkorDB (Railway) drops idle sockets
                logger.warning("Library graph write failed for %s (attempt %d/3): %s", name, attempt + 1, exc)
                time.sleep((2, 5, 0)[attempt])
        logger.info("Library: %s -> %s (%.2f), %d contract(s), %d person(s), %d passage(s)",
                    name, result.category, result.confidence, len(result.contracts),
                    len(result.people), passages)
        return {"item_id": item_id, "status": status, "category": result.category}
    except Exception as exc:  # noqa: BLE001
        if self.request.retries < self.max_retries:
            # Hand it back as pending so the retry can claim it again.
            store.mark(organization_id, item_id, status=PENDING)
            logger.warning("Library: %s failed (attempt %d), retrying: %s",
                           name, self.request.retries + 1, str(exc)[:200])
            raise self.retry(exc=exc, countdown=30 * (self.request.retries + 1))
        store.mark(organization_id, item_id, status=FAILED, error=str(exc)[:300])
        logger.warning("Library: %s failed for good: %s", name, exc)
        return {"item_id": item_id, "status": FAILED}


def sync_library(organization_id: str, nodes: list[dict]) -> dict:
    """Called by the structure sync right after the folder map is rebuilt: reconcile the
    library rows with the crawl, put the existing analysis back on the fresh graph, and queue
    whatever is new or changed. Never raises — the structure sync already succeeded."""
    from client.crm_store import get_crm_store
    from client.library_graph import relink, remove_items

    store = get_library_store()
    try:
        diff = store.sync_files(organization_id, [n for n in nodes if n.get("type") == "file"],
                                get_crm_store().bid_folder_names(organization_id))
        relink(organization_id, store.analysed(organization_id, with_embedding=True))
        # A file read before its folder became a bid's still has passages the agents could find.
        remove_items(organization_id, diff["removed"] + diff["output"])
        analyse_library_task.delay(organization_id)
        return {"new_or_changed": len(diff["to_analyse"]), "removed": len(diff["removed"])}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Library sync failed for org %s: %s", organization_id, exc, exc_info=True)
        return {"error": str(exc)[:200]}

