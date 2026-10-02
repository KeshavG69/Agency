"""relationship_nudges — the drafted outreach the relationship engine produces.

One row per (owner, contact, kind): the nightly sweep UPSERTS on `dedupe_key`, so re-running
it refreshes a still-open nudge (new draft, fresh cadence numbers) rather than piling up
duplicates — the same idempotency contract the action planner uses. An Action of kind
relationship_touch / relationship_personal points here by id; the Today card reads the draft
and its evidence from this collection.

WHY A HUMAN DECISION IS FINAL. Once a rep approves or dismisses a nudge it leaves the open
set for good — the sweep never reopens the one the rep already dealt with. That is what stops
the engine nagging someone about a message they already sent or waved off.

HOW A CONTACT BECOMES NUDGE-ABLE AGAIN. The `dedupe_key` carries a CYCLE TOKEN (the contact's
`last_contact` day at draft time — see tasks/relationship_tasks.py). So the same contact is
silenced only for the current silence; once the relationship actually moves — they reply, or
the rep's sent mail lands — `last_contact` advances, and if they go quiet again that is a new
cycle with a new key and a new nudge. Without that token the key was static per
(owner, contact, kind), which silenced every contact permanently after one decision and made
the engine go quiet forever after a single pass over the network.

A DECIDED ROW IS IMMUTABLE. `upsert` refreshes the draft only while the nudge is open; after
a decision the stored subject/body is the record of what the human actually approved.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument

from app.settings import settings

logger = logging.getLogger(__name__)

OPEN, APPROVED, DISMISSED, EXPIRED = "open", "approved", "dismissed", "expired"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


class NudgeStore:
    def __init__(self) -> None:
        client = MongoClient(settings.MONGODB_URL, tz_aware=True)
        self.db = client[settings.MONGODB_DATABASE]
        self.nudges = self.db["relationship_nudges"]

    def upsert(self, nudge: dict) -> Optional[str]:
        """Write one nudge idempotently on `dedupe_key`.

        A nudge the rep has already ACTED ON (approved/dismissed) is never reopened: the
        conditional keeps a decided status, and only refreshes the draft/evidence on a row
        that is still open (or being created). Returns the nudge id.
        """
        key = (nudge.get("dedupe_key") or "").strip()
        if not key:
            return None
        now = _utc_now()
        # Fields refreshed only while the nudge is still OPEN. Once a rep has approved or
        # dismissed it, the row is the RECORD OF WHAT THEY ACTED ON — overwriting the draft
        # on the next sweep would rewrite history, so that a nudge the rep approved and sent
        # would later show a different message than the one that actually went out.
        refreshable = (
            "subject", "body", "reason", "drew_on", "contact_name", "contact_company",
            "corr_count", "days_since", "overdue_by", "last_subject", "last_contact",
            "hook",
        )
        # Identity — constant for a given dedupe_key, safe to keep re-asserting.
        identity = ("owner_email", "kind", "contact_email", "organization_id")

        sets: dict = {k: nudge.get(k) for k in identity}
        for k in refreshable:
            # On insert `$status` is missing, so $ifNull yields OPEN and the new value wins.
            # $literal: inside an aggregation expression any string starting with "$" is
            # read as a FIELD PATH. A model-written subject like "$5M recompete" would then
            # null the field or fail the write outright.
            sets[k] = {
                "$cond": [
                    {"$eq": [{"$ifNull": ["$status", OPEN]}, OPEN]},
                    {"$literal": nudge.get(k)},
                    f"${k}",  # decided: keep what the human actually saw
                ]
            }
        sets.update({
            "updated_at": now,
            "created_at": {"$ifNull": ["$created_at", now]},
            # Insert -> open; a decided row keeps its decision, an open row stays open.
            "status": {"$ifNull": ["$status", OPEN]},
        })

        doc = self.nudges.find_one_and_update(
            {"dedupe_key": key},
            [{"$set": sets}],
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return str(doc["_id"]) if doc else None

    def existing_keys(self, organization_id: str, owner_email: str) -> dict[str, dict]:
        """dedupe_key -> {"id", "status"} for everything we already hold for this employee.

        The sweep reads this BEFORE drafting so it never pays a model call for a nudge that
        already exists: an open one keeps the draft the rep may already have read, and a
        decided one must not be re-offered at all. The id comes back too, so the sweep can
        retire open nudges whose cycle has ended.
        """
        rows = self.nudges.find(
            {"organization_id": (organization_id or "").strip(),
             "owner_email": (owner_email or "").strip().lower()},
            {"dedupe_key": 1, "status": 1, "hook": 1},
        )
        return {
            r["dedupe_key"]: {"id": str(r["_id"]), "status": r.get("status") or OPEN,
                              "hook": r.get("hook")}
            for r in rows if r.get("dedupe_key")
        }

    def refresh_signals(self, dedupe_key: str, signals: dict) -> bool:
        """Update ONLY the cadence numbers on an open nudge — not the draft.

        The facts age every day ("quiet 47 days" becomes 48); the message does not need to be
        rewritten for that, and rewriting it would change a draft under a rep who already
        read it. Never touches a decided row.
        """
        allowed = ("corr_count", "days_since", "overdue_by", "reason",
                   "last_subject", "last_contact")
        fields = {k: signals[k] for k in allowed if k in signals}
        if not fields:
            return False
        fields["updated_at"] = _utc_now()
        res = self.nudges.update_one(
            {"dedupe_key": dedupe_key, "status": OPEN}, {"$set": fields}
        )
        return res.modified_count > 0

    def list_open(self, organization_id: str, owner_email: str) -> list[dict]:
        """One employee's open nudges — most overdue first, then most recent. This is what
        the action planner reads to emit the Today cards."""
        rows = self.nudges.find(
            {
                "organization_id": (organization_id or "").strip(),
                "owner_email": (owner_email or "").strip().lower(),
                "status": OPEN,
            }
        ).sort([("overdue_by", -1), ("updated_at", -1)])
        return [_serialize(r) for r in rows]

    def open_for_org(self, organization_id: str) -> list[dict]:
        """Every open nudge in the org (all owners) — what the action planner reads to emit
        the Today cards. Most overdue first."""
        rows = self.nudges.find(
            {"organization_id": (organization_id or "").strip(), "status": OPEN}
        ).sort([("overdue_by", -1), ("updated_at", -1)])
        return [_serialize(r) for r in rows]

    def close_open_by_ref(self, organization_id: str, ref_ids: list[str]) -> int:
        """Expire open nudges whose ids are in `ref_ids` — used to retire nudges the sweep no
        longer regenerates. Not used on the human-decide path (that is `decide`)."""
        ids = []
        for r in ref_ids or []:
            try:
                ids.append(ObjectId(r))
            except Exception:  # noqa: BLE001
                continue
        if not ids:
            return 0
        res = self.nudges.update_many(
            {"organization_id": (organization_id or "").strip(), "_id": {"$in": ids},
             "status": OPEN},
            {"$set": {"status": EXPIRED, "updated_at": _utc_now()}},
        )
        return res.modified_count

    def get(self, nudge_id: str, organization_id: str) -> Optional[dict]:
        try:
            oid = ObjectId(nudge_id)
        except Exception:  # noqa: BLE001 — a bad id is a 404, not a 500
            return None
        doc = self.nudges.find_one(
            {"_id": oid, "organization_id": (organization_id or "").strip()}
        )
        return _serialize(doc) if doc else None

    def decide(self, nudge_id: str, organization_id: str, status: str) -> Optional[dict]:
        """A rep approves or dismisses one nudge. Terminal — the sweep never reopens it."""
        if status not in (APPROVED, DISMISSED):
            return None
        try:
            oid = ObjectId(nudge_id)
        except Exception:  # noqa: BLE001
            return None
        doc = self.nudges.find_one_and_update(
            {"_id": oid, "organization_id": (organization_id or "").strip()},
            {"$set": {"status": status, "decided_at": _utc_now(), "updated_at": _utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        return _serialize(doc) if doc else None


_store: Optional[NudgeStore] = None
_lock = threading.RLock()


def get_nudge_store() -> NudgeStore:
    global _store
    with _lock:
        if _store is None:
            _store = NudgeStore()
        return _store
