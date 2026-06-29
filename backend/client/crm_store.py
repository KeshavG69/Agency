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

    def get_document(self, document_id: str) -> dict | None:
        try:
            doc = self.documents.find_one({"_id": ObjectId(document_id)})
        except Exception:  # noqa: BLE001 — malformed id
            return None
        return _serialize(doc) if doc else None

    def update_document(self, document_id: str, **fields) -> None:
        """Update a document (e.g. status -> 'filed', new url, bumped version)."""
        fields["updated_at"] = _utc_now()
        self.documents.update_one({"_id": ObjectId(document_id)}, {"$set": fields})

    # --- assignment -------------------------------------------------------
    def set_assignment(
        self, opportunity_id: str, organization_id: str, user_ids: list[str]
    ) -> bool:
        """Assign an opportunity to zero or more members (by user id). Org-scoped."""
        try:
            res = self.opps.update_one(
                {"_id": ObjectId(opportunity_id), "organization_id": organization_id},
                {"$set": {"assigned_to": [str(u) for u in user_ids]}},
            )
        except Exception:  # noqa: BLE001 — malformed id
            return False
        return res.matched_count > 0

    @staticmethod
    def _visibility_query(organization_id: str, viewer_id: str | None, is_admin: bool) -> dict:
        """Org filter + (for non-admins) only opps assigned to the viewer or unassigned."""
        q: dict = {"organization_id": organization_id}
        if not is_admin and viewer_id:
            q["$or"] = [
                {"assigned_to": viewer_id},               # array contains the viewer
                {"assigned_to": {"$in": [None, []]}},     # explicitly unassigned
                {"assigned_to": {"$exists": False}},      # never assigned
            ]
        return q

    # --- reads for the UI -------------------------------------------------
    def list_all(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True
    ) -> list[dict]:
        """This org's opportunities (highest priority first). Non-admins see only the ones
        assigned to them or unassigned."""
        q = self._visibility_query(organization_id, viewer_id, is_admin)
        cursor = self.opps.find(q).sort("priority_score", -1)
        return [_serialize(d) for d in cursor]

    def list_all_enriched(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True
    ) -> list[dict]:
        """list_all + each opp's documents/calls/tasks attached — but BATCHED into a
        handful of queries (not 3-per-opp), so it scales to a large SAM.gov pull instead
        of firing thousands of round-trips at the DB."""
        from collections import defaultdict

        opps = self.list_all(organization_id, viewer_id=viewer_id, is_admin=is_admin)
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

    # --- consolidated call plan (across the whole pipeline) ----------------
    def call_plan(self, organization_id: str) -> list[dict]:
        """Every planned call across this org's opportunities, joined with opportunity
        context — the consolidated BD call sheet, sorted by priority (then nearest deadline)."""
        opps = {o["id"]: o for o in self.list_all(organization_id)}
        if not opps:
            return []
        rows: list[dict] = []
        for c in self.calls.find({"opportunity_id": {"$in": list(opps.keys())}}):
            sc = _serialize(c)
            opp = opps.get(c["opportunity_id"], {})
            rows.append({
                "call_id": sc["id"],
                "opportunity_id": c["opportunity_id"],
                "opportunity_title": opp.get("title"),
                "agency": opp.get("agency"),
                "priority_score": opp.get("priority_score"),
                "bid_decision": opp.get("bid_decision"),
                "response_deadline": opp.get("response_deadline"),
                "poc_name": opp.get("poc_name"),
                "poc_email": opp.get("poc_email"),
                "name": sc.get("name"),
                "talking_point": sc.get("talking_point"),
                "status": sc.get("status") or "Planned",
                "created_at": sc.get("created_at"),
            })
        rows.sort(
            key=lambda r: (r.get("priority_score") or -1, r.get("response_deadline") or "9999"),
            reverse=True,
        )
        return rows

    # --- outreach log (collision detection) -------------------------------
    def log_outreach(
        self, organization_id: str, contact_email: str, employee_email: str, action: str,
        opportunity_id: str | None = None, opportunity_title: str | None = None,
    ) -> None:
        """Record that an employee drafted/sent outreach to a contact — powers the
        'someone is already talking to this person' warning."""
        ce = (contact_email or "").strip().lower()
        if not ce:
            return
        self.db["outreach_log"].insert_one({
            "organization_id": organization_id,
            "contact_email": ce,
            "employee_email": (employee_email or "").strip().lower(),
            "action": action,  # "drafted" | "sent"
            "opportunity_id": opportunity_id,
            "opportunity_title": opportunity_title,
            "created_at": _utc_now(),
        })

    def outreach_collisions(
        self, organization_id: str, emails: list[str], exclude_employee: str
    ) -> dict[str, list[dict]]:
        """For each contact email, the latest outreach by OTHER employees in the org →
        {email: [{employee_email, action, opportunity_title, created_at}, ...]} (one per
        other employee, newest first). Empty when nobody else has engaged the contact."""
        wanted = [e.strip().lower() for e in emails if e and e.strip()]
        if not wanted:
            return {}
        exclude = (exclude_employee or "").strip().lower()
        out: dict[str, dict[str, dict]] = {}  # email -> employee -> latest event
        cursor = self.db["outreach_log"].find({
            "organization_id": organization_id,
            "contact_email": {"$in": wanted},
        }).sort("created_at", -1)
        for r in cursor:
            emp = r.get("employee_email")
            if not emp or emp == exclude:
                continue
            by_emp = out.setdefault(r["contact_email"], {})
            if emp not in by_emp:  # cursor is newest-first → first seen is the latest
                by_emp[emp] = {
                    "employee_email": emp,
                    "action": r.get("action"),
                    "opportunity_title": r.get("opportunity_title"),
                    "created_at": r.get("created_at"),
                }
        return {email: list(emps.values()) for email, emps in out.items()}

    def set_call_status(self, call_id: str, organization_id: str, status: str) -> bool:
        """Update a call's status (Planned / Done / Dismissed). Org-scoped: the call's
        opportunity must belong to the caller's org."""
        try:
            call = self.calls.find_one({"_id": ObjectId(call_id)})
        except Exception:  # noqa: BLE001 — malformed id
            return False
        if not call:
            return False
        if self.get_opportunity(call.get("opportunity_id", ""), organization_id) is None:
            return False
        self.calls.update_one(
            {"_id": ObjectId(call_id)},
            {"$set": {"status": status, "updated_at": _utc_now()}},
        )
        return True


_store: CRMStore | None = None
_lock = threading.RLock()


def get_crm_store() -> CRMStore:
    global _store
    with _lock:
        if _store is None:
            _store = CRMStore()
        return _store
