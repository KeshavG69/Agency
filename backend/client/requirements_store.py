"""proposal_requirements — one reviewed checklist per opportunity.

The agent's outline is a DRAFT. A person corrects it, ticks items off and attaches files, then
approves it, so the row holds human work as well as machine output. That is why a re-read
(an amendment arrived) MERGES instead of replacing: ticks, notes and files survive on any item
that is still required, and items a person added by hand are never removed.

Status: reading -> ready -> approved; or failed. A re-read of an approved list returns it to
`ready`, because the requirements may have changed and need looking at again.

A small summary (`requirements_status/done/total/approved_at`) is mirrored onto the opportunity
so the pipeline and the task planner can read it without a second query.
"""
from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo import ReturnDocument

from client.mongo import mongo_db
from models.requirements import ProposalOutline

logger = logging.getLogger(__name__)

READING, READY, APPROVED, FAILED = "reading", "ready", "approved", "failed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _new_item(**fields) -> dict:
    return {"id": uuid.uuid4().hex[:12], "done": False, "note": "", "files": [], "gaps": [],
            "gen_status": None, "added_by_person": False, **fields}


class RequirementsStore:
    def __init__(self) -> None:
        self.db = mongo_db(tz_aware=True)
        self.rows = self.db["proposal_requirements"]
        self.opps = self.db["opportunities"]

    def _mirror(self, row: dict) -> None:
        items = row.get("items") or []
        self.opps.update_one(
            {"_id": ObjectId(row["opportunity_id"]), "organization_id": row["organization_id"]},
            {"$set": {
                "requirements_status": row.get("status"),
                "requirements_done": sum(1 for i in items if i.get("done")),
                "requirements_total": len(items),
                "requirements_approved_at": row.get("approved_at"),
            }},
        )

    def get(self, organization_id: str, opportunity_id: str) -> Optional[dict]:
        return self.rows.find_one(
            {"organization_id": organization_id, "opportunity_id": opportunity_id},
            {"_id": 0, "source_text": 0})

    def source_text(self, organization_id: str, opportunity_id: str) -> str:
        """The package as it was read, so each writer doesn't re-download and re-parse it."""
        row = self.rows.find_one({"organization_id": organization_id, "opportunity_id": opportunity_id},
                                 {"source_text": 1})
        return (row or {}).get("source_text") or ""

    def _update(self, organization_id: str, opportunity_id: str, update: dict) -> Optional[dict]:
        row = self.rows.find_one_and_update(
            {"organization_id": organization_id, "opportunity_id": opportunity_id}, update,
            projection={"_id": 0, "source_text": 0}, return_document=ReturnDocument.AFTER,
        )
        if row:
            self._mirror(row)
        return row

    def mark_reading(self, organization_id: str, opportunity_id: str) -> None:
        now = _utc_now()
        row = self.rows.find_one_and_update(
            {"organization_id": organization_id, "opportunity_id": opportunity_id},
            {"$set": {"status": READING, "error": None, "updated_at": now},
             "$setOnInsert": {"items": [], "created_at": now}},
            upsert=True, projection={"_id": 0, "source_text": 0}, return_document=ReturnDocument.AFTER,
        )
        self._mirror(row)

    def mark_failed(self, organization_id: str, opportunity_id: str, error: str) -> None:
        self._update(organization_id, opportunity_id,
                     {"$set": {"status": FAILED, "error": error[:300], "updated_at": _utc_now()}})

    def save_outline(self, organization_id: str, opportunity_id: str, outline: ProposalOutline,
                     files: dict, source_text: str = "") -> Optional[dict]:
        """Store a fresh reading, carrying a person's work across to the items that remain."""
        old = self.get(organization_id, opportunity_id) or {}
        by_title = {_key(i.get("title")): i for i in old.get("items") or []}
        items = []
        for it in outline.items:
            prev = by_title.pop(_key(it.title), None) or {}
            item = _new_item(**it.model_dump())
            for keep in ("id", "done", "note", "files", "gaps", "gen_status"):
                if keep in prev:
                    item[keep] = prev[keep]
            items.append(item)
        # What a person added stays. What they ticked or attached but the new reading no longer
        # lists stays too, flagged, so it can't vanish without anyone noticing.
        for leftover in by_title.values():
            if leftover.get("added_by_person"):
                items.append(leftover)
            elif leftover.get("done") or leftover.get("files"):
                items.append({**leftover, "dropped_by_reread": True})
        body = outline.model_dump(exclude={"items"})
        return self._update(organization_id, opportunity_id, {"$set": {
            **body, "items": items, "files": files, "source_text": source_text,
            "status": READY, "error": None,
            "read_at": _utc_now(), "approved_at": None, "approved_by": None, "updated_at": _utc_now(),
        }})

    def edit_item(self, organization_id: str, opportunity_id: str, item_id: str, fields: dict) -> Optional[dict]:
        sets = {f"items.$.{k}": v for k, v in fields.items()}
        sets["updated_at"] = _utc_now()
        row = self.rows.find_one_and_update(
            {"organization_id": organization_id, "opportunity_id": opportunity_id, "items.id": item_id},
            {"$set": sets}, projection={"_id": 0, "source_text": 0}, return_document=ReturnDocument.AFTER,
        )
        if row:
            self._mirror(row)
        return row

    # A write in flight stamps `gen_at` at each stage (queued, writing, drafted, reworded). One that
    # stops stamping died with its worker (a deploy or restart kills the task mid-run, and nothing
    # else ever moves the line on), so the line would sit on "Writing…" with its button disabled
    # forever. The slowest single stage is the writer: three tries of up to 5 minutes. Queued work
    # can legitimately wait behind a big package.
    _STUCK_MINUTES = {"writing": 20, "queued": 60}

    def expire_stuck(self, organization_id: str, opportunity_id: str, row: Optional[dict]) -> Optional[dict]:
        """Mark writes that stopped making progress as failed, so they can be started again."""
        now = _utc_now()
        for item in (row or {}).get("items") or []:
            limit = self._STUCK_MINUTES.get(item.get("gen_status") or "")
            since = item.get("gen_at") or (row or {}).get("updated_at")
            if not limit or not since:
                continue
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            if (now - since).total_seconds() > limit * 60:
                logger.warning("Write of %s/%s stuck on %s since %s: marking it failed",
                               opportunity_id, item.get("id"), item.get("gen_status"), since)
                row = self.edit_item(organization_id, opportunity_id, item["id"], {
                    "gen_status": "failed",
                    "gen_error": "The write was interrupted (the server restarted). Write it again.",
                }) or row
        return row

    def add_writer_note(self, organization_id: str, opportunity_id: str, item_id: str, text: str,
                        by: str) -> Optional[dict]:
        """What a person told the writer for this document. Notes add up: an answer given for one
        rewrite still holds for the next."""
        return self.rows.find_one_and_update(
            {"organization_id": organization_id, "opportunity_id": opportunity_id, "items.id": item_id},
            {"$push": {"items.$.writer_notes": {"text": text, "by": by, "at": _utc_now()}},
             "$set": {"updated_at": _utc_now()}},
            projection={"_id": 0, "source_text": 0}, return_document=ReturnDocument.AFTER,
        )

    def add_item(self, organization_id: str, opportunity_id: str, title: str, kind: str, detail: str) -> Optional[dict]:
        item = _new_item(title=title, kind=kind, detail=detail, page_limit=None, format=None,
                         source_ref=None, quote="", verified=True, added_by_person=True)
        return self._update(organization_id, opportunity_id,
                            {"$push": {"items": item}, "$set": {"updated_at": _utc_now()}})

    def remove_item(self, organization_id: str, opportunity_id: str, item_id: str) -> Optional[dict]:
        return self._update(organization_id, opportunity_id,
                            {"$pull": {"items": {"id": item_id}}, "$set": {"updated_at": _utc_now()}})

    def set_email(self, organization_id: str, opportunity_id: str, email: dict) -> Optional[dict]:
        return self._update(organization_id, opportunity_id,
                            {"$set": {"email": email, "updated_at": _utc_now()}})

    def approve(self, organization_id: str, opportunity_id: str, by_email: str) -> Optional[dict]:
        return self._update(organization_id, opportunity_id, {"$set": {
            "status": APPROVED, "approved_at": _utc_now(), "approved_by": by_email, "updated_at": _utc_now(),
        }})


_store: Optional[RequirementsStore] = None
_lock = threading.RLock()


def get_requirements_store() -> RequirementsStore:
    global _store
    with _lock:
        if _store is None:
            _store = RequirementsStore()
        return _store
