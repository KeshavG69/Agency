"""MongoDB store — Collecct's CRM / pipeline source of truth.

Replaces the EspoCRM client. Stores opportunities (and calls/tasks) as plain
documents in the canonical snake_case shape, so ingestion + agents work with no
field mapping. Thread-safe singleton, mirroring the PriceIQ CRUD pattern.

Exposes the SAME method names the rest of the code already calls:
upsert_opportunity, count, list_unanalyzed_opportunities, apply_verdict,
create_call, create_task.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient

from app.settings import settings
from models.opportunity import Opportunity
from models.verdict import AnalystVerdict


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: dict) -> dict:
    """Mongo _id (ObjectId) -> string 'id'."""
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


class CRMStore:
    def __init__(self):
        client = MongoClient(settings.MONGODB_URL)
        self.db = client[settings.MONGODB_DATABASE]
        self.opps = self.db["opportunities"]
        self.calls = self.db["calls"]
        self.tasks = self.db["tasks"]
        self.documents = self.db["documents"]
        # Per-ORG uniqueness on solicitation number — two orgs may legitimately
        # track the same solicitation, so uniqueness is (organization_id, sol#).
        # Drop the legacy GLOBAL unique index if it's still present from before.
        try:
            self.opps.drop_index("solicitation_number_1")
        except Exception:
            pass
        self.opps.create_index(
            [("organization_id", 1), ("solicitation_number", 1)],
            unique=True,
            partialFilterExpression={"solicitation_number": {"$type": "string"}},
        )
        self.opps.create_index("organization_id")
        self.opps.create_index("notice_id")
        self.opps.create_index("analyzed_at")
        # Fetch all calls/tasks/documents for an opportunity without scanning.
        self.calls.create_index("opportunity_id")
        self.tasks.create_index("opportunity_id")
        self.documents.create_index("opportunity_id")
        self.documents.create_index([("opportunity_id", 1), ("type", 1)])

    # --- ingestion --------------------------------------------------------
    def upsert_opportunity(self, opp: Opportunity, organization_id: str) -> tuple[str, str]:
        """Insert, or update an existing one (matched within the org by sol#/notice id).

        Every opportunity is tagged with `organization_id` so reads can be org-scoped.
        Returns (action, id) where action is 'created' or 'updated'.
        """
        doc = opp.model_dump()
        doc.setdefault("stage", "Discover")
        doc["organization_id"] = organization_id

        key = None
        if opp.solicitation_number:
            key = {"organization_id": organization_id, "solicitation_number": opp.solicitation_number}
        elif opp.notice_id:
            key = {"organization_id": organization_id, "notice_id": opp.notice_id}

        if key:
            existing = self.opps.find_one(key, {"_id": 1})
            if existing:
                # Refresh the source opportunity fields, but never clobber the
                # analyst's own state (stage placement + verdict) on re-upload.
                doc.pop("stage", None)
                self.opps.update_one({"_id": existing["_id"]}, {"$set": doc})
                return "updated", str(existing["_id"])

        res = self.opps.insert_one(doc)
        return "created", str(res.inserted_id)

    def count(self, organization_id: str | None = None) -> int:
        return self.opps.count_documents(
            {"organization_id": organization_id} if organization_id else {}
        )

    # --- analyst support --------------------------------------------------
    def list_unanalyzed_opportunities(self, organization_id: str) -> list[dict]:
        """Opportunities in this org the Analyst hasn't scored yet (no analyzed_at)."""
        cursor = self.opps.find({
            "organization_id": organization_id,
            "$or": [{"analyzed_at": {"$exists": False}}, {"analyzed_at": None}],
        })
        return [_serialize(d) for d in cursor]

    def get_opportunity(self, opportunity_id: str, organization_id: str) -> dict | None:
        """Fetch one opportunity by id, scoped to the org (None if not found / other org)."""
        try:
            oid = ObjectId(opportunity_id)
        except Exception:
            return None  # malformed id — treat as not found, never raise
        doc = self.opps.find_one({"_id": oid, "organization_id": organization_id})
        return _serialize(doc) if doc else None

    # --- capture gate (human approval) ------------------------------------
    def mark_capture_approved(self, opportunity_id: str) -> None:
        """Human-approve an opportunity for capture (lets the capture agents run)."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)}, {"$set": {"capture_approved": True}}
        )

    def list_capture_ready(self, organization_id: str) -> list[dict]:
        """Approved opportunities in this org the capture pipeline hasn't processed yet."""
        cursor = self.opps.find({
            "organization_id": organization_id,
            "capture_approved": True,
            "$or": [{"captured_at": {"$exists": False}}, {"captured_at": None}],
        })
        return [_serialize(d) for d in cursor]

    def mark_captured(self, opportunity_id: str) -> None:
        """Mark capture done so the opportunity isn't re-processed."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)}, {"$set": {"captured_at": _utc_now()}}
        )

    def set_recommended_contacts(self, opportunity_id: str, contacts: list[dict]) -> None:
        """Store the CRM Agent's ranked relevant contacts on the opportunity.

        `contacts_searched_at` is set even when the list is empty, so the UI can
        tell 'searched, none relevant' apart from 'not searched yet'.
        """
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {
                "recommended_contacts": contacts,
                "contacts_searched_at": _utc_now(),
            }},
        )

    def set_outreach_drafts(self, opportunity_id: str, drafts: list[dict]) -> None:
        """Store the Mail Agent's per-contact outreach drafts on the opportunity.

        `outreach_drafted_at` is set even when the list is empty, so the UI can tell
        'generated, nothing to send' apart from 'not generated yet'.
        """
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {
                "outreach_drafts": drafts,
                "outreach_drafted_at": _utc_now(),
            }},
        )

    def upsert_outreach_draft(self, opportunity_id: str, draft: dict) -> None:
        """Replace the draft addressed to draft['to'] (case-insensitive), or append it.

        Used when the user regenerates ONE contact's email — only that draft changes.
        """
        to = (draft.get("to") or "").lower()
        doc = self.opps.find_one({"_id": ObjectId(opportunity_id)}, {"outreach_drafts": 1})
        drafts = (doc or {}).get("outreach_drafts") or []
        for i, d in enumerate(drafts):
            if (d.get("to") or "").lower() == to:
                drafts[i] = draft
                break
        else:
            drafts.append(draft)
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {"outreach_drafts": drafts, "outreach_drafted_at": _utc_now()}},
        )

    def apply_verdict(self, opportunity_id: str, verdict: AnalystVerdict) -> None:
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {
                "bid_decision": verdict.bid_decision,
                "priority_score": verdict.priority_score,
                "analyst_rationale": verdict.rationale,
                "stage": verdict.recommended_stage,
                "analyzed_at": _utc_now(),
            }},
        )

    # Bid_decision -> pipeline stage, mirroring the Analyst's mapping.
    _DECISION_STAGE = {"Bid": "Qualify", "Watch": "Discover", "No-Bid": "No-Bid"}

    def set_decision(self, opportunity_id: str, organization_id: str, decision: str) -> bool:
        """Human override of the Analyst's bid_decision (Bid / Watch / No-Bid).

        Stamps `analyzed_at` so the Analyst batch won't clobber the manual call, and
        flags `decision_overridden` for the UI. Org-scoped: only the owning org can set it.
        """
        res = self.opps.update_one(
            {"_id": ObjectId(opportunity_id), "organization_id": organization_id},
            {"$set": {
                "bid_decision": decision,
                "stage": self._DECISION_STAGE.get(decision, "Discover"),
                "decision_overridden": True,
                "decision_overridden_at": _utc_now(),
                "analyzed_at": _utc_now(),
            }},
        )
        return res.matched_count > 0

    def create_call(self, opportunity_id: str, name: str, talking_point: str) -> str:
        res = self.calls.insert_one({
            "opportunity_id": opportunity_id,
            "name": name,
            "talking_point": talking_point,
            "status": "Planned",
            "created_at": _utc_now(),
        })
        return str(res.inserted_id)

    def create_task(
        self, opportunity_id: str, name: str, description: str = "",
        due_date: str | None = None,
    ) -> str:
        res = self.tasks.insert_one({
            "opportunity_id": opportunity_id,
            "name": name,
            "description": description,
            "due_date": due_date,
            "status": "Not Started",
            "created_at": _utc_now(),
        })
        return str(res.inserted_id)

    # --- documents (capture plans, white papers, RFI responses, ...) ------
    def create_document(
        self, opportunity_id: str, agent_id: str, doc_type: str, title: str,
        url: str, status: str = "draft", version: int = 1,
    ) -> str:
        """Record a generated document (a pointer — the file lives in iDrive/SharePoint)."""
        now = _utc_now()
        res = self.documents.insert_one({
            "opportunity_id": opportunity_id,
            "agent_id": agent_id,
            "type": doc_type,
            "title": title,
            "url": url,
            "status": status,        # draft | approved | filed
            "version": version,
            "created_at": now,
            "updated_at": now,
        })
        return str(res.inserted_id)

    def list_documents(self, opportunity_id: str, doc_type: str | None = None) -> list[dict]:
        query: dict = {"opportunity_id": opportunity_id}
        if doc_type:
            query["type"] = doc_type
        cursor = self.documents.find(query).sort("created_at", -1)
        return [_serialize(d) for d in cursor]

    def update_document(self, document_id: str, **fields) -> None:
        """Update a document (e.g. status -> 'filed', new url, bumped version)."""
        fields["updated_at"] = _utc_now()
        self.documents.update_one({"_id": ObjectId(document_id)}, {"$set": fields})

    # --- reads for the UI -------------------------------------------------
    def list_all(self, organization_id: str) -> list[dict]:
        """All of this org's opportunities (highest priority first)."""
        cursor = self.opps.find({"organization_id": organization_id}).sort("priority_score", -1)
        return [_serialize(d) for d in cursor]

    def list_all_enriched(self, organization_id: str) -> list[dict]:
        """list_all + each opp's documents/calls/tasks attached — but BATCHED into a
        handful of queries (not 3-per-opp), so it scales to a large SAM.gov pull instead
        of firing thousands of round-trips at the DB."""
        from collections import defaultdict

        opps = self.list_all(organization_id)
        ids = [o["id"] for o in opps]
        if not ids:
            return opps
        docs: dict[str, list] = defaultdict(list)
        calls: dict[str, list] = defaultdict(list)
        tasks: dict[str, list] = defaultdict(list)
        for d in self.documents.find({"opportunity_id": {"$in": ids}}).sort("created_at", -1):
            docs[d["opportunity_id"]].append(_serialize(d))
        for c in self.calls.find({"opportunity_id": {"$in": ids}}):
            calls[c["opportunity_id"]].append(_serialize(c))
        for t in self.tasks.find({"opportunity_id": {"$in": ids}}):
            tasks[t["opportunity_id"]].append(_serialize(t))
        for o in opps:
            o["documents"] = docs.get(o["id"], [])
            o["calls"] = calls.get(o["id"], [])
            o["tasks"] = tasks.get(o["id"], [])
        return opps

    def list_calls(self, opportunity_id: str) -> list[dict]:
        cursor = self.calls.find({"opportunity_id": opportunity_id})
        return [_serialize(d) for d in cursor]

    def list_tasks(self, opportunity_id: str) -> list[dict]:
        cursor = self.tasks.find({"opportunity_id": opportunity_id})
        return [_serialize(d) for d in cursor]


_store: CRMStore | None = None
_lock = threading.RLock()


def get_crm_store() -> CRMStore:
    global _store
    with _lock:
        if _store is None:
            _store = CRMStore()
        return _store
