"""library_documents — what the Library agent learned about each SharePoint file.

One row per (organization_id, item_id). This is the SOURCE OF TRUTH for the analysis; the
knowledge graph only mirrors it. That split is deliberate: every SharePoint sync clears and
rebuilds the folder map from scratch, and an analysis costs a model call per file, so it must
survive the rebuild. After each sync the graph is re-linked from these rows (no model calls),
and only files whose content changed (new `version`) are read again.

ACCESS. Each row carries the file's SharePoint roster (`permitted_emails` / `org_wide`),
refreshed on every sync, so every read below can filter to what one employee may open —
the same rule as the structure graph's `_ACCESS` predicate.

A PERSON'S CORRECTION IS STICKY. `category_override` is set when someone fixes a category; a
later re-read (because the file changed) keeps that category and only refreshes the facts.

Statuses: pending -> analysing -> done | needs_review | failed; or skipped (not readable).
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from pymongo import UpdateOne

from app.settings import settings
from client.mongo import mongo_db

logger = logging.getLogger(__name__)

UPLOAD = "upload"  # `source` of a document uploaded straight into Collecct

# `shelf` of a file inside a bid's SharePoint folder: the third library, what we're producing now.
# It's listed for people but kept from the agents. Those are unreviewed drafts, and a writer that
# could cite one could launder its own invented figure into a "fact" for the next bid.
OUTPUT = "output"

PENDING, ANALYSING, DONE, REVIEW, FAILED, SKIPPED = (
    "pending", "analysing", "done", "needs_review", "failed", "skipped",
)

# What LiteParse can turn into text. Images are left out on purpose: a logo or a screenshot
# costs an OCR pass and never yields a category worth having.
READABLE_EXT = {
    "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "rtf", "csv",
    "htm", "html", "odt", "ods", "odp", "md",
}

# Never returned to the frontend or the agents — large, and only the search needs it.
_HIDDEN = {"_id": 0, "summary_embedding": 0, "item_embeddings": 0, "object_key": 0, "source_text": 0}

_WITH_VECTORS = {"_id": 0, "source_text": 0}  # for the search: vectors yes, whole files no

# The parsed text kept with each row, so a proposal writer's claim can be checked against the
# WHOLE file it cites (the passages in the graph stop at LIBRARY_READ_CHARS).
SOURCE_TEXT_CHARS = 1_500_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _acl(email: Optional[str]) -> dict:
    """Mongo filter for 'this employee may read it' (no filter for an admin / system read)."""
    if not email:
        return {}
    return {"$or": [{"org_wide": True}, {"permitted_emails": email.lower()}]}


def skip_reason(node: dict) -> Optional[str]:
    """Why a file won't be read, or None when it will."""
    ext = (node.get("ext") or "").lower()
    if ext not in READABLE_EXT:
        return f"File type .{ext or '?'} isn't a document we read"
    size = node.get("size") or 0
    if size > settings.LIBRARY_MAX_FILE_MB * 1024 * 1024:
        return f"Larger than {settings.LIBRARY_MAX_FILE_MB} MB"
    return None


class LibraryStore:
    def __init__(self) -> None:
        self.db = mongo_db(tz_aware=True)
        self.docs = self.db["library_documents"]

    # ---- sync --------------------------------------------------------------------------
    def sync_files(self, organization_id: str, file_nodes: list[dict],
                   bid_folders: Iterable[str] = ()) -> dict:
        """Reconcile the rows with a fresh crawl's file nodes.

        Refreshes every file's name/path/link/ACL; marks new or content-changed files pending;
        marks unreadable ones skipped; deletes rows for files no longer in SharePoint.
        `bid_folders` are the names of the bids' folders: what's inside them is the output shelf.
        Returns {"to_analyse": [item_id...], "removed": [item_id...], "output": [item_id...]}.
        """
        org = organization_id
        bids = set(bid_folders)
        existing = {
            d["item_id"]: d
            for d in self.docs.find(
                # Uploaded files aren't in SharePoint, so a crawl must never treat them as deleted.
                {"organization_id": org, "source": {"$ne": UPLOAD}},
                {"item_id": 1, "analysed_version": 1, "status": 1, "graph_pending": 1},
            )
        }
        now = _utc_now()
        ops: list[UpdateOne] = []
        to_analyse: list[str] = []
        output: list[str] = []
        seen: set[str] = set()
        for n in file_nodes:
            iid = n.get("id")
            if not iid:
                continue
            seen.add(iid)
            meta = {
                "drive_id": n.get("drive_id"), "site_id": n.get("site_id"),
                "name": n.get("name"), "path": n.get("path"), "web_url": n.get("web_url"),
                "ext": (n.get("ext") or "").lower() or None, "size": n.get("size"),
                "version": n.get("version"), "modified": n.get("modified"),
                "permitted_emails": [e.lower() for e in (n.get("permitted_emails") or [])],
                "org_wide": bool(n.get("org_wide")),
                "shelf": OUTPUT if bids & set((n.get("path") or "").split("/")) else None,
                "updated_at": now,
            }
            if meta["shelf"] == OUTPUT:
                output.append(iid)
            row = existing.get(iid)
            reason = skip_reason(n)
            if reason:
                meta.update(status=SKIPPED, skip_reason=reason)
            elif row is None or (
                n.get("version") and row.get("analysed_version") != n.get("version")
                and row.get("status") != ANALYSING
            ) or row.get("status") in (SKIPPED, FAILED) or row.get("graph_pending"):
                # New, changed, newly readable (e.g. the size limit was raised), a past failure
                # worth one more try, or an analysis whose passages never reached the graph.
                meta.update(status=PENDING, skip_reason=None, error=None)
                to_analyse.append(iid)
            ops.append(UpdateOne(
                {"organization_id": org, "item_id": iid},
                {"$set": meta, "$setOnInsert": {"created_at": now}},
                upsert=True,
            ))
        if ops:
            self.docs.bulk_write(ops, ordered=False)
        removed = [iid for iid in existing if iid not in seen]
        if removed:
            self.docs.delete_many({"organization_id": org, "item_id": {"$in": removed}})
        return {"to_analyse": to_analyse, "removed": removed, "output": output}

    # ---- one document ------------------------------------------------------------------
    def get(self, organization_id: str, item_id: str, email: Optional[str] = None,
            with_embedding: bool = False) -> Optional[dict]:
        q = {"organization_id": organization_id, "item_id": item_id, **_acl(email)}
        return self.docs.find_one(q, _WITH_VECTORS if with_embedding else _HIDDEN)

    def claim(self, organization_id: str, item_id: str) -> Optional[dict]:
        """Atomically move a pending row to analysing; None if another worker already has it."""
        return self.docs.find_one_and_update(
            {"organization_id": organization_id, "item_id": item_id, "status": PENDING},
            {"$set": {"status": ANALYSING, "started_at": _utc_now()}},
        )

    def mark(self, organization_id: str, item_id: str, **fields) -> None:
        fields["updated_at"] = _utc_now()
        self.docs.update_one({"organization_id": organization_id, "item_id": item_id}, {"$set": fields})

    def set_category_override(self, organization_id: str, item_id: str, category: str,
                              by_email: str) -> bool:
        """A person's category fix: sticks across re-reads; queues a re-read for its facts."""
        r = self.docs.update_one(
            {"organization_id": organization_id, "item_id": item_id, "status": {"$ne": SKIPPED}},
            {"$set": {
                "category_override": category, "category": category, "confidence": 1.0,
                "corrected_by": by_email, "corrected_at": _utc_now(),
                "status": PENDING, "updated_at": _utc_now(),
            }},
        )
        return r.matched_count > 0

    # ---- reads -------------------------------------------------------------------------
    def list(self, organization_id: str, email: Optional[str] = None, category: Optional[str] = None,
             status: Optional[str] = None, limit: int = 2000) -> list[dict]:
        q: dict = {"organization_id": organization_id, **_acl(email)}
        if category:
            q["category"] = category
        if status:
            q["status"] = status
        return list(self.docs.find(q, _HIDDEN).sort("path", 1).limit(limit))

    def analysed(self, organization_id: str, email: Optional[str] = None,
                 categories: Iterable[str] | None = None, with_embedding: bool = False) -> list[dict]:
        """Rows the agents may use: a usable analysis (done or needs_review), off the output
        shelf, optionally by category."""
        q: dict = {"organization_id": organization_id, "status": {"$in": [DONE, REVIEW]},
                   "shelf": {"$ne": OUTPUT}, **_acl(email)}
        if categories:
            q["category"] = {"$in": list(categories)}
        return list(self.docs.find(q, _WITH_VECTORS if with_embedding else _HIDDEN))

    def counts(self, organization_id: str, email: Optional[str] = None) -> dict:
        """{"status": {status: n}, "category": {category: n}} for the progress line + filters."""
        base = {"organization_id": organization_id, **_acl(email)}
        by_status = {r["_id"]: r["n"] for r in self.docs.aggregate([
            {"$match": base}, {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ])}
        by_category = {r["_id"]: r["n"] for r in self.docs.aggregate([
            {"$match": {**base, "status": {"$in": [DONE, REVIEW]}}},
            {"$group": {"_id": "$category", "n": {"$sum": 1}}},
        ])}
        return {"status": by_status, "category": by_category}

    def source_text(self, organization_id: str, item_id: str) -> str:
        """The file's parsed text, for proving a claim against it. Files read before the text
        was kept fall back to their passages in the graph."""
        row = self.docs.find_one({"organization_id": organization_id, "item_id": item_id},
                                 {"source_text": 1})
        if row and row.get("source_text"):
            return row["source_text"]
        from client.library_graph import passages_text

        return passages_text(organization_id, item_id)

    def pending_ids(self, organization_id: str) -> list[str]:
        return [d["item_id"] for d in self.docs.find(
            {"organization_id": organization_id, "status": PENDING}, {"item_id": 1})]

    def requeue_stuck(self, organization_id: str, older_than_minutes: int = 30) -> int:
        """Rows left 'analysing' by a worker that died mid-read go back to pending."""
        from datetime import timedelta

        cutoff = _utc_now() - timedelta(minutes=older_than_minutes)
        r = self.docs.update_many(
            {"organization_id": organization_id, "status": ANALYSING, "started_at": {"$lt": cutoff}},
            {"$set": {"status": PENDING}},
        )
        return r.modified_count

    def delete_org(self, organization_id: str) -> int:
        """Disconnecting SharePoint forgets what came from SharePoint. Uploads were never there."""
        return self.docs.delete_many(
            {"organization_id": organization_id, "source": {"$ne": UPLOAD}}).deleted_count

    def add_upload(self, organization_id: str, name: str, size: int, object_key: str,
                   by_email: str) -> dict:
        """A company document uploaded straight into Collecct, for orgs without SharePoint.
        Readable by the whole org (`org_wide`): there is no SharePoint roster to copy."""
        now = _utc_now()
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else None
        row = {
            "organization_id": organization_id, "item_id": uuid.uuid4().hex, "source": UPLOAD,
            "object_key": object_key, "name": name, "path": f"Uploaded/{name}", "ext": ext,
            "size": size, "web_url": None, "drive_id": None, "site_id": None, "version": "upload",
            "permitted_emails": [], "org_wide": True, "uploaded_by": by_email,
            "created_at": now, "updated_at": now,
        }
        reason = skip_reason(row)
        row.update(status=SKIPPED, skip_reason=reason) if reason else row.update(status=PENDING)
        self.docs.insert_one(row)
        row.pop("_id", None)
        return row

    def add_sharepoint_file(self, organization_id: str, item: dict, by_email: str) -> dict:
        """A file just put in SharePoint from Collecct. It's listed now so the person can follow
        it; the crawl that follows fills in its roster and queues the reading."""
        now = _utc_now()
        name = item.get("name") or "document"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else None
        meta = {
            "drive_id": (item.get("parentReference") or {}).get("driveId"), "name": name,
            "path": f"Company Library/{name}", "web_url": item.get("webUrl"), "ext": ext,
            "size": item.get("size"), "uploaded_by": by_email, "updated_at": now,
        }
        reason = skip_reason(meta)
        meta.update(status=SKIPPED, skip_reason=reason) if reason else meta.update(status=PENDING, skip_reason=None, error=None)
        self.docs.update_one(
            {"organization_id": organization_id, "item_id": item["id"]},
            {"$set": meta, "$setOnInsert": {"permitted_emails": [], "org_wide": True, "created_at": now}},
            upsert=True,
        )
        return {**meta, "item_id": item["id"]}

    def remove_upload(self, organization_id: str, item_id: str) -> Optional[dict]:
        return self.docs.find_one_and_delete(
            {"organization_id": organization_id, "item_id": item_id, "source": UPLOAD}, {"_id": 0})


_store: Optional[LibraryStore] = None
_lock = threading.RLock()


def get_library_store() -> LibraryStore:
    global _store
    with _lock:
        if _store is None:
            _store = LibraryStore()
        return _store
