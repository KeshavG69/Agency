"""agent_tasks — the durable to-do list that lets work be scheduled for LATER.

Until now Collecct had exactly two ways for work to start: the daily clock (SAM.gov at
11:00, SharePoint at 08:00) and a human clicking a button. That is why the things the
system already notices get forgotten — `company_needs_research` is written and never
read, a "Watch" verdict leaves a card nothing re-opens.

This is the third way: a row that says WHAT to do, for WHICH org, WHEN it is due, and
WHY. A tick every minute leases whatever is due and hands it to a worker.

    enqueue(org, "research_company", "company", "nexagen.com", "unknown company")
    ...a minute later...
    claim_due(...) -> the row, leased for 30 minutes
    complete_task(id, "Defense IT services, per nexagen.com/about")

WHY LEASES AND NOT A CELERY QUEUE
Celery already gives us durability and retries — that is not the gap. What Celery cannot
express is "run this when it becomes DUE, at most once across N workers, and give it back
if the worker dies mid-run". A Celery queue means *run now*. `lease_until` is what makes a
crashed run self-heal: the lease simply expires and the row becomes claimable again.

The claim is `find_one_and_update`, which is atomic per document — Mongo's equivalent of
Postgres' `SELECT ... FOR UPDATE SKIP LOCKED`. Two ticks racing can never take the same row.

See docs/enrichment-implementation-plan.md §5.6.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument

from app.settings import settings

logger = logging.getLogger(__name__)

# Three goes, then we stop paying for the same failure forever.
MAX_ATTEMPTS = 3

# Anything a human is waiting for outranks anything the system decided to do on its own.
PRIORITY: dict[str, int] = {
    "requested": 300,   # a rep clicked something and is watching a spinner
    "meeting": 200,     # a meeting is in the diary; there is a deadline
    "signature": 150,   # cheap, mechanical, improves everything downstream
    "identify": 100,    # a new contact arrived
    "sweep": 50,        # background tidying; nobody is waiting
    "recheck": 0,       # "look at this again some day"
}

# Kinds that need an LLM (slow, costly, long lease) vs mechanical work (fast, no model).
# Two lanes so a signature parse never queues behind a five-minute research run.
LLM_KINDS: tuple[str, ...] = ("research_company", "recheck_opportunity", "call_brief")
DIRECT_KINDS: tuple[str, ...] = ("parse_signatures",)

LEASE_LLM_MS = 30 * 60_000
LEASE_DIRECT_MS = 2 * 60_000
BATCH_LLM = 12
BATCH_DIRECT = 60

# A fruitless lookup must not be retried tomorrow: the answer rarely changes, and the
# search costs money every time it is asked.
STAND_DOWN_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


class TaskStore:
    def __init__(self) -> None:
        # tz_aware so datetimes come BACK from Mongo timezone-aware. Without it pymongo
        # returns naive datetimes and any Python-side comparison against an aware `now()`
        # raises — a subtle bug this store would hit the first time a lease was inspected.
        client = MongoClient(settings.MONGODB_URL, tz_aware=True)
        self.db = client[settings.MONGODB_DATABASE]
        self.tasks = self.db["agent_tasks"]
        # Indexes live in utils/db_indexes.py: the de-dup lookup ("already queued or recently
        # done?") and the claim query (open, due, unleased, highest priority first).

    # --- putting work on the list ---------------------------------------------------

    def enqueue(
        self,
        organization_id: str,
        kind: str,
        subject_type: str,
        subject_id: str,
        reason: str,
        *,
        priority: Optional[int] = None,
        budget: int = 4,
        due_at: Optional[datetime] = None,
        cooldown_days: int = 0,
    ) -> Optional[str]:
        """Add a job. Returns its id, or None if it was de-duplicated.

        `cooldown_days` is the stand-down: skip if the same work FINISHED that recently.
        Pass 30 for a lookup whose answer rarely changes (company research); pass 0 for
        work that is meant to recur (an opportunity re-check).
        """
        org = (organization_id or "").strip()
        sid = (subject_id or "").strip().lower()
        if not org or not kind or not sid:
            return None

        scope = {"organization_id": org, "kind": kind, "subject.id": sid}

        # Already queued and not finished — nothing to add.
        if self.tasks.find_one({**scope, "finished_at": None}):
            return None

        # Finished recently enough that asking again would just re-buy the same answer.
        if cooldown_days > 0:
            since = _utc_now() - timedelta(days=cooldown_days)
            if self.tasks.find_one({**scope, "finished_at": {"$gte": since}}):
                return None

        now = _utc_now()
        doc = {
            "organization_id": org,
            "kind": kind,
            "subject": {"type": subject_type, "id": sid},
            "reason": reason or "",
            "priority": PRIORITY["sweep"] if priority is None else priority,
            "budget": budget,
            "due_at": due_at or now,
            "lease_until": None,
            "attempts": 0,
            "finished_at": None,
            "outcome": None,
            "created_at": now,
        }
        return str(self.tasks.insert_one(doc).inserted_id)

    def enqueue_many(
        self, organization_id: str, kind: str, subject_type: str,
        subject_ids: Sequence[str], reason: str, **kwargs,
    ) -> int:
        """Queue a batch, skipping anything already queued or inside its stand-down."""
        return sum(
            1
            for sid in dict.fromkeys(subject_ids)  # de-dup, order preserved
            if sid and self.enqueue(organization_id, kind, subject_type, sid, reason, **kwargs)
        )

    # --- taking work off the list ---------------------------------------------------

    def claim_due(
        self, limit: int, kinds: Sequence[str], lease_ms: int
    ) -> list[dict]:
        """Lease up to `limit` due rows. Atomic per row, so concurrent ticks never collide.

        Claiming increments `attempts` up front: a worker that dies without reporting back
        has still spent an attempt, which is what stops a poisonous row being retried for
        the lifetime of the install.
        """
        if not kinds or limit <= 0:
            return []
        now = _utc_now()
        until = now + timedelta(milliseconds=lease_ms)
        claimed: list[dict] = []

        for _ in range(limit):
            doc = self.tasks.find_one_and_update(
                {
                    "finished_at": None,
                    "due_at": {"$lte": now},
                    "attempts": {"$lt": MAX_ATTEMPTS},
                    "kind": {"$in": list(kinds)},
                    # Unleased, or the previous holder's lease has expired (it crashed).
                    "$or": [{"lease_until": None}, {"lease_until": {"$lt": now}}],
                },
                {"$set": {"lease_until": until}, "$inc": {"attempts": 1}},
                sort=[("priority", -1), ("due_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if not doc:
                break
            claimed.append(_serialize(doc))  # type: ignore[arg-type]
        return claimed

    def claim_one(self, task_id: str, lease_ms: int) -> Optional[dict]:
        """Lease ONE specific row by id — for user-triggered work that should run immediately
        instead of waiting for the next tick. Same atomic lease + attempt bump as `claim_due`,
        so the tick can never also claim it, and a crash still self-heals when the lease
        expires. Returns None if the row is gone, finished, exhausted, or already leased
        (i.e. the tick beat us to it — which is fine, it will run there)."""
        oid = _oid(task_id)
        if not oid:
            return None
        now = _utc_now()
        until = now + timedelta(milliseconds=lease_ms)
        doc = self.tasks.find_one_and_update(
            {
                "_id": oid,
                "finished_at": None,
                "attempts": {"$lt": MAX_ATTEMPTS},
                "$or": [{"lease_until": None}, {"lease_until": {"$lt": now}}],
            },
            {"$set": {"lease_until": until}, "$inc": {"attempts": 1}},
            return_document=ReturnDocument.AFTER,
        )
        return _serialize(doc) if doc else None

    # --- finishing -------------------------------------------------------------------

    def complete_task(self, task_id: str, outcome: str) -> bool:
        """Done — with a one-line record of what happened, in words a rep could read."""
        oid = _oid(task_id)
        if not oid:
            return False
        return self.tasks.update_one(
            {"_id": oid, "finished_at": None},
            {"$set": {"finished_at": _utc_now(), "outcome": (outcome or "")[:500],
                      "lease_until": None}},
        ).modified_count > 0

    def stand_down(self, task_id: str, days: int, why: str) -> bool:
        """Found nothing. Push it out rather than finishing it, so the answer is looked
        for again eventually — but not tomorrow, and not at today's price."""
        oid = _oid(task_id)
        if not oid:
            return False
        return self.tasks.update_one(
            {"_id": oid, "finished_at": None},
            {"$set": {"due_at": _utc_now() + timedelta(days=days), "lease_until": None,
                      "attempts": 0, "reason": (why or "")[:500]}},
        ).modified_count > 0

    def retire_exhausted(self) -> list[dict]:
        """Close out rows that burned every attempt and never reported back — otherwise
        they sit in the table forever, invisible and never retried."""
        now = _utc_now()
        stale = list(self.tasks.find({
            "finished_at": None,
            "attempts": {"$gte": MAX_ATTEMPTS},
            "$or": [{"lease_until": None}, {"lease_until": {"$lt": now}}],
        }))
        for doc in stale:
            self.tasks.update_one(
                {"_id": doc["_id"], "finished_at": None},
                {"$set": {
                    "finished_at": now,
                    "outcome": f"Gave up after {MAX_ATTEMPTS} attempts: never reported back.",
                }},
            )
        return [_serialize(d) for d in stale]  # type: ignore[misc]

    # --- reads ------------------------------------------------------------------------

    def open_tasks(self, organization_id: str, limit: int = 100) -> list[dict]:
        rows = self.tasks.find(
            {"organization_id": (organization_id or "").strip(), "finished_at": None}
        ).sort([("priority", -1), ("due_at", 1)]).limit(limit)
        return [_serialize(r) for r in rows]  # type: ignore[misc]


def _oid(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except Exception:  # noqa: BLE001 — a bad id is a miss, not a crash
        return None


_store: Optional[TaskStore] = None
_lock = threading.RLock()


def get_task_store() -> TaskStore:
    global _store
    with _lock:
        if _store is None:
            _store = TaskStore()
        return _store
