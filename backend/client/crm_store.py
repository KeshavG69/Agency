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
        # Unique only for real (string) solicitation numbers — opportunities
        # without one (null) are excluded from the index, so they don't collide.
        self.opps.create_index(
            "solicitation_number",
            unique=True,
            partialFilterExpression={"solicitation_number": {"$type": "string"}},
        )
        self.opps.create_index("notice_id")
        self.opps.create_index("analyzed_at")
        # Fetch all calls/tasks/documents for an opportunity without scanning.
        self.calls.create_index("opportunity_id")
        self.tasks.create_index("opportunity_id")
        self.documents.create_index("opportunity_id")
        self.documents.create_index([("opportunity_id", 1), ("type", 1)])

    # --- ingestion --------------------------------------------------------
    def upsert_opportunity(self, opp: Opportunity) -> tuple[str, str]:
        """Insert, or update an existing one matched by solicitation #/notice id.

        Returns (action, id) where action is 'created' or 'updated'.
        """
        doc = opp.model_dump()
        doc.setdefault("stage", "Discover")

        key = None
        if opp.solicitation_number:
            key = {"solicitation_number": opp.solicitation_number}
        elif opp.notice_id:
            key = {"notice_id": opp.notice_id}

        if key:
            existing = self.opps.find_one(key, {"_id": 1})
            if existing:
                self.opps.update_one({"_id": existing["_id"]}, {"$set": doc})
                return "updated", str(existing["_id"])

        res = self.opps.insert_one(doc)
        return "created", str(res.inserted_id)

    def count(self) -> int:
        return self.opps.count_documents({})

    # --- analyst support --------------------------------------------------
    def list_unanalyzed_opportunities(self) -> list[dict]:
        """Opportunities the Analyst hasn't scored yet (no analyzed_at)."""
        cursor = self.opps.find(
            {"$or": [{"analyzed_at": {"$exists": False}}, {"analyzed_at": None}]}
        )
        return [_serialize(d) for d in cursor]

    # --- capture gate (human approval) ------------------------------------
    def mark_capture_approved(self, opportunity_id: str) -> None:
        """Human-approve an opportunity for capture (lets the capture agents run)."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)}, {"$set": {"capture_approved": True}}
        )

    def list_capture_ready(self) -> list[dict]:
        """Approved opportunities the capture pipeline hasn't processed yet."""
        cursor = self.opps.find({
            "capture_approved": True,
            "$or": [{"captured_at": {"$exists": False}}, {"captured_at": None}],
        })
        return [_serialize(d) for d in cursor]

    def mark_captured(self, opportunity_id: str) -> None:
        """Mark capture done so the opportunity isn't re-processed."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)}, {"$set": {"captured_at": _utc_now()}}
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
    def list_all(self) -> list[dict]:
        """All opportunities (highest priority first)."""
        cursor = self.opps.find().sort("priority_score", -1)
        return [_serialize(d) for d in cursor]

    def list_calls(self, opportunity_id: str) -> list[dict]:
        cursor = self.calls.find({"opportunity_id": opportunity_id})
        return [_serialize(d) for d in cursor]


_store: CRMStore | None = None
_lock = threading.RLock()


def get_crm_store() -> CRMStore:
    global _store
    with _lock:
        if _store is None:
            _store = CRMStore()
        return _store
