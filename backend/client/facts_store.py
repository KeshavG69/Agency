"""contact_facts — what we know about a contact, how sure we are, and who said so.

Every fact carries the evidence it came from. Strong evidence is APPLIED (written onto
the record); weak evidence is PROPOSED (offered to a rep as a suggestion under an empty
field, settled with one click). That distinction is the whole point: a confidently wrong
title on a contracting officer is worse than a blank field, because nobody can tell it
is wrong.

THE THREE INVARIANTS, ENFORCED HERE IN CODE — NOT IN A PROMPT
  1. Never overwrite a value a human typed. A human decision outranks every source.
  2. Never re-offer a value a human dismissed. Dismissal is permanent for that value.
  3. Never write to the record without a PRIMARY source (see models/evidence.py).
A prompt can ask for these; only code can guarantee them, and agents are exactly the
kind of caller that will politely ignore an instruction on its four hundredth turn.

EVIDENCE ACCUMULATES. Re-recording the same (field, value) MERGES the new evidence into
the existing row and re-scores the union, so a claim can be promoted over time:

    signature says "VP, Business Development"      -> 0.80  PROPOSED (a suggestion)
    ...later, they reply to us from that address   -> 0.97  APPLIED  (now a fact)

SCOPING: facts are keyed (organization_id, email) — org-level, never global and never
per-employee. A job title is the same truth for everyone in the org, so one lookup
benefits every rep; but nothing ever crosses an organisation boundary, because the
evidence behind it is derived from that org's own mailbox.

See docs/enrichment-implementation-plan.md §5.3.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from bson import ObjectId
from pymongo import MongoClient, UpdateMany, UpdateOne
from pymongo.errors import BulkWriteError

from app.settings import settings
from models.evidence import Evidence, score_evidence

logger = logging.getLogger(__name__)

# Fields a fact may describe. Anything else is rejected, so a stray agent cannot invent
# columns on a contact record.
FACT_FIELDS = frozenset(
    {"title", "company", "industry", "phone", "seniority", "function", "linkedin", "website"}
)

APPLIED, PROPOSED, DISMISSED, SUPERSEDED = "APPLIED", "PROPOSED", "DISMISSED", "SUPERSEDED"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


def _merge_evidence(
    existing: Sequence[dict], incoming: Sequence[Evidence]
) -> list[dict]:
    """Union, de-duplicated the same way the scorer counts: two observations from the
    same source are ONE observation. Without this, re-running enrichment would inflate
    a single page into false certainty simply by being run twice."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in [*existing, *incoming]:
        if not item or not item.get("kind"):
            continue
        key = (item["kind"], (item.get("source_url") or item.get("detail") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "kind": item["kind"],
                "detail": item.get("detail") or "",
                **({"source_url": item["source_url"]} if item.get("source_url") else {}),
            }
        )
    return out


@dataclass(frozen=True)
class FactOutcome:
    """What happened, in words a log (or later, the Agent tab) can show verbatim."""

    stored: bool
    status: Optional[str]
    reason: str
    score: float = 0.0
    band: Optional[str] = None


class FactsStore:
    def __init__(self) -> None:
        client = MongoClient(settings.MONGODB_URL)
        self.db = client[settings.MONGODB_DATABASE]
        self.facts = self.db["contact_facts"]
        # One row per distinct claim; re-recording merges into it rather than duplicating.
        self.facts.create_index(
            [("organization_id", 1), ("email", 1), ("field", 1), ("value", 1)], unique=True
        )
        # The read path: everything we hold on one contact, split by status.
        self.facts.create_index([("organization_id", 1), ("email", 1), ("status", 1)])
        # The org-wide review queue: filter (organization_id, status), sort by score desc.
        # WITHOUT the third key Mongo must load every PROPOSED row in the org and sort it
        # in memory — fine at fifty suggestions, a cliff once a mailbox sweep has produced
        # thousands. With it, the index IS the sort order and the query stops at `limit`.
        self.facts.create_index(
            [("organization_id", 1), ("status", 1), ("score", -1)]
        )

    # --- write path ---------------------------------------------------------------

    def record_fact(
        self,
        organization_id: str,
        email: str,
        field: str,
        value: Optional[str],
        evidence: Sequence[Evidence],
    ) -> FactOutcome:
        """Record one observation. Returns WHY it was stored, proposed, or skipped."""
        org = (organization_id or "").strip()
        addr = (email or "").strip().lower()
        val = (value or "").strip()
        if not org or not addr or field not in FACT_FIELDS or not val:
            return FactOutcome(False, None, "missing organization, email, field or value")

        key = {"organization_id": org, "email": addr, "field": field, "value": val}

        # (2) A human said no. Their decision is final for this value — never re-offer it.
        existing = self.facts.find_one(key)
        if existing and existing.get("status") == DISMISSED:
            return FactOutcome(False, DISMISSED, "a human dismissed this value")

        merged = _merge_evidence(existing.get("evidence", []) if existing else [], evidence)
        scored = score_evidence(merged)  # type: ignore[arg-type]

        # (3) Too weak to be worth anyone's attention. Not stored at all — a blank field
        # is a better outcome than a claim nobody should act on.
        if scored.band is None:
            return FactOutcome(False, None, scored.rationale or "too weak to store", scored.score)

        # (1) A human owns this field. A stronger source does not get to overrule them.
        current = self.facts.find_one(
            {"organization_id": org, "email": addr, "field": field, "status": APPLIED}
        )
        if current and current.get("decided_by") and current.get("value") != val:
            return FactOutcome(
                False, PROPOSED, "a human set this field", scored.score, scored.band
            )

        status = APPLIED if scored.band == "VERIFIED" else PROPOSED
        now = _utc_now()
        self.facts.update_one(
            key,
            {
                "$set": {
                    "value": val,
                    "score": scored.score,
                    "band": scored.band,
                    "rationale": scored.rationale,
                    "evidence": merged,
                    "status": status,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now, "decided_by": None},
            },
            upsert=True,
        )

        # A newer verified value retires the old one rather than deleting it — which is
        # also how a job change shows up for free, as a SUPERSEDED row.
        if status == APPLIED:
            self.facts.update_many(
                {
                    "organization_id": org,
                    "email": addr,
                    "field": field,
                    "status": APPLIED,
                    "value": {"$ne": val},
                },
                {"$set": {"status": SUPERSEDED, "updated_at": now}},
            )
        return FactOutcome(True, status, scored.rationale, scored.score, scored.band)

    def record_bulk(
        self,
        organization_id: str,
        claims: Sequence[tuple[str, str, Optional[str], Sequence[Evidence]]],
    ) -> dict[str, int]:
        """Record many claims across many contacts in a HANDFUL of round trips.

        `claims` is (email, field, value, evidence) tuples.

        WHY THIS EXISTS: `record_fact` costs ~4 round trips (dismissed check, human-owned
        check, upsert, supersede). That is fine for the one-at-a-time paths — an agent
        writing a result, a rep accepting a suggestion — but ingestion writes ~4 facts for
        each of thousands of contacts. Measured against the production Mongo at 414 ms RTT
        that came to 1.8 s per fact and a projected FIVE HOURS for one mailbox. Here the
        same work is 2 queries + 1 bulk write per batch, regardless of size.

        The three invariants are identical to `record_fact` — they are simply evaluated in
        memory against one pre-loaded snapshot instead of being re-queried per claim:
          1. never overwrite a human-set value
          2. never re-offer a dismissed value
          3. never write to the record without a primary source (the band gate)
        `scripts/test_facts_store.py` asserts bulk and single agree.
        """
        org = (organization_id or "").strip()
        if not org or not claims:
            return {"applied": 0, "proposed": 0, "skipped": 0}

        # Normalise + drop anything structurally invalid before touching the network.
        wanted: list[tuple[str, str, str, Sequence[Evidence]]] = []
        for email, field, value, evidence in claims:
            addr = (email or "").strip().lower()
            val = (value or "").strip()
            if addr and field in FACT_FIELDS and val:
                wanted.append((addr, field, val, evidence))
        if not wanted:
            return {"applied": 0, "proposed": 0, "skipped": 0}

        emails = sorted({c[0] for c in wanted})

        # ROUND TRIP 1: every row we already hold for these contacts.
        existing: dict[tuple[str, str, str], dict] = {}
        applied_now: dict[tuple[str, str], dict] = {}
        for row in self.facts.find(
            {"organization_id": org, "email": {"$in": emails}},
            {"email": 1, "field": 1, "value": 1, "status": 1, "decided_by": 1, "evidence": 1},
        ):
            existing[(row["email"], row["field"], row["value"])] = row
            if row.get("status") == APPLIED:
                applied_now[(row["email"], row["field"])] = row

        now = _utc_now()
        ops: list = []
        supersede: set[tuple[str, str, str]] = set()
        tally = {"applied": 0, "proposed": 0, "skipped": 0}

        for addr, field, val, evidence in wanted:
            prior = existing.get((addr, field, val))

            # (2) dismissed is permanent for this value
            if prior and prior.get("status") == DISMISSED:
                tally["skipped"] += 1
                continue

            merged = _merge_evidence(prior.get("evidence", []) if prior else [], evidence)
            scored = score_evidence(merged)  # type: ignore[arg-type]
            if scored.band is None:  # (3) too weak to store at all
                tally["skipped"] += 1
                continue

            # (1) a human owns this field
            current = applied_now.get((addr, field))
            if current and current.get("decided_by") and current.get("value") != val:
                tally["skipped"] += 1
                continue

            status = APPLIED if scored.band == "VERIFIED" else PROPOSED
            ops.append(UpdateOne(
                {"organization_id": org, "email": addr, "field": field, "value": val},
                {
                    "$set": {
                        "value": val, "score": scored.score, "band": scored.band,
                        "rationale": scored.rationale, "evidence": merged,
                        "status": status, "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now, "decided_by": None},
                },
                upsert=True,
            ))
            if status == APPLIED:
                supersede.add((addr, field, val))
                # Keep the in-memory snapshot honest: a later claim for the same field in
                # this same batch must see what we just decided, not the pre-batch state.
                applied_now[(addr, field)] = {"value": val, "decided_by": None}
                tally["applied"] += 1
            else:
                tally["proposed"] += 1

        for addr, field, val in supersede:
            ops.append(UpdateMany(
                {"organization_id": org, "email": addr, "field": field,
                 "status": APPLIED, "value": {"$ne": val}},
                {"$set": {"status": SUPERSEDED, "updated_at": now}},
            ))

        # ROUND TRIP 2: everything at once. ordered=False so one duplicate-key collision
        # cannot abandon the rest of the batch.
        if ops:
            try:
                self.facts.bulk_write(ops, ordered=False)
            except BulkWriteError as exc:  # noqa: BLE001 — report, do not lose the batch
                logger.warning("record_bulk: %d write error(s)", len(exc.details.get("writeErrors", [])))
        return tally

    def record_many(
        self, organization_id: str, email: str, items: Sequence[tuple[str, Optional[str]]],
        evidence: Sequence[Evidence],
    ) -> dict[str, FactOutcome]:
        """Record several fields that share one observation (e.g. everything a single
        signature block stated). Plain loop — no agent, no network."""
        out: dict[str, FactOutcome] = {}
        for field, value in items:
            if value:
                out[field] = self.record_fact(organization_id, email, field, value, evidence)
        return out

    # --- the human decision (the ONLY router-owned mutation) -----------------------

    def decide_fact(
        self, organization_id: str, fact_id: str, accept: bool, user_email: str
    ) -> Optional[dict]:
        """A rep accepts or dismisses a suggestion. Accepting marks it human-owned, which
        is what makes invariant (1) bite from then on."""
        try:
            oid = ObjectId(fact_id)
        except Exception:  # noqa: BLE001 — a bad id from the client is a 404, not a 500
            return None
        doc = self.facts.find_one({"_id": oid, "organization_id": (organization_id or "").strip()})
        if not doc:
            return None

        now = _utc_now()
        self.facts.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": APPLIED if accept else DISMISSED,
                    "decided_by": (user_email or "").strip().lower(),
                    "decided_at": now,
                    "updated_at": now,
                }
            },
        )
        if accept:
            self.facts.update_many(
                {
                    "organization_id": doc["organization_id"],
                    "email": doc["email"],
                    "field": doc["field"],
                    "status": APPLIED,
                    "_id": {"$ne": oid},
                },
                {"$set": {"status": SUPERSEDED, "updated_at": now}},
            )
        return _serialize(self.facts.find_one({"_id": oid}) or {})

    # --- read path ------------------------------------------------------------------

    def applied_facts(self, organization_id: str, email: str) -> dict[str, str]:
        """{field: value} for everything solid enough to show as fact."""
        rows = self.facts.find(
            {
                "organization_id": (organization_id or "").strip(),
                "email": (email or "").strip().lower(),
                "status": APPLIED,
            },
            {"field": 1, "value": 1},
        )
        return {r["field"]: r["value"] for r in rows}

    def suggestions(self, organization_id: str, email: str) -> list[dict]:
        """Open suggestions for one contact — what a rep is asked to settle."""
        rows = self.facts.find(
            {
                "organization_id": (organization_id or "").strip(),
                "email": (email or "").strip().lower(),
                "status": PROPOSED,
            }
        ).sort("score", -1)
        return [_serialize(r) for r in rows]


_store: Optional[FactsStore] = None
_lock = threading.RLock()


def get_facts_store() -> FactsStore:
    global _store
    with _lock:
        if _store is None:
            _store = FactsStore()
        return _store
