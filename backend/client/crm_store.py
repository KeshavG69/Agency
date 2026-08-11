"""MongoDB store — Collecct's CRM / pipeline source of truth.

Replaces the EspoCRM client. Stores opportunities (and calls/tasks) as plain
documents in the canonical snake_case shape, so ingestion + agents work with no
field mapping. Thread-safe singleton, mirroring the PriceIQ CRUD pattern.

Exposes the SAME method names the rest of the code already calls:
upsert_opportunity, count, list_unanalyzed_opportunities, apply_verdict,
create_call, create_task.
"""
from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from app.settings import settings
from models.opportunity import Opportunity
from models.verdict import AnalystVerdict


# Cursor batch size for reads that fetch a bounded working set in one go.
#
# The driver's default first batch is 101 documents; anything past that costs another
# round trip to the cluster, which on this deployment measures ~1.3s. These reads return a
# few hundred small documents at most, so pulling them in a single batch is strictly better
# than paying a second hop for the tail. It is NOT a blanket default: an unbounded scan
# should keep the small default so it streams instead of buffering.
_ONE_BATCH = 1000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_today() -> date:
    """Today in UTC — the day boundary the whole product runs on.

    Deliberately not `date.today()`, which is the SERVER's local date and can be a day ahead
    of UTC. The daily action plan is scheduled in days, so an off-by-one there silently moves
    every task a day.
    """
    return datetime.now(timezone.utc).date()


def _serialize(doc: dict) -> dict:
    """Mongo _id (ObjectId) -> string 'id'."""
    if doc and "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return doc


# The fields the pipeline LIST (rows + facets + calendar + status/activity chips) needs — and
# ONLY those. Heavy fields (document_text, analyst_rationale, extra, description, and the
# documents/calls/tasks joins) are deliberately omitted; they load lazily in the detail pane via
# get_opportunity_enriched(). Keeps a list page tiny (~a few hundred bytes/opp) instead of ~4KB.
# A hard ceiling on any single page. Nothing in the product needs more, and an
# unbounded `limit` is how a list endpoint becomes a 10 MB response on the one customer
# with the most data — which is exactly what the deprecated list-everything endpoint
# above did (~10 MB / 9.5 s). Enforced in the store so no caller can opt out.
MAX_PAGE_SIZE = 100

SLIM_PROJECTION: dict[str, int] = {f: 1 for f in (
    "title", "solicitation_number", "notice_id", "agency", "naics", "psc_code", "set_aside",
    "opp_type", "posted_date", "response_deadline", "estimated_value", "place_of_performance",
    "stage", "source", "link", "priority_score", "bid_decision", "decision_overridden",
    "analyzed_at", "capture_approved", "captured_at", "capture_error", "ingesting", "ingest_error",
    "assigned_to", "contacts_searched_at", "outreach_drafted_at", "poc_name", "poc_email",
    "organization_id",
)}


class CRMStore:
    def __init__(self):
        client = MongoClient(settings.MONGODB_URL)
        self.db = client[settings.MONGODB_DATABASE]
        self.opps = self.db["opportunities"]
        self.calls = self.db["calls"]
        self.tasks = self.db["tasks"]
        self.documents = self.db["documents"]
        self.mail_triage = self.db["mail_triage"]
        # Org-level call briefs, one per (org, opportunity, requesting rep) — the rep's own
        # mailbox is the source, so two reps prepping the same call get their own brief.
        self.call_briefs = self.db["call_briefs"]
        # The daily action plan: one row per thing a human must do, on a given day. See
        # models/action.py for why this is its own collection and not `agent_tasks`.
        self.actions = self.db["actions"]
        # Indexes live in utils/db_indexes.py (the single source of truth) — applied on app
        # startup and by `python -m utils.db_indexes`. Note the per-ORG uniqueness on
        # solicitation number: two orgs may legitimately track the same solicitation.
        # Drop the legacy GLOBAL unique index if it's still present from before.
        try:
            self.opps.drop_index("solicitation_number_1")
        except Exception:
            pass

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

    def insert_opportunity(self, opp: Opportunity, organization_id: str) -> str:
        """Always insert a NEW opportunity (no dedup) — used for manual creation.

        Unlike upsert_opportunity, this never matches/updates an existing row on a
        colliding solicitation_number (which would clobber that row's fields), so a
        manual add is always a fresh, analyzable opportunity. Returns the new id.
        """
        doc = opp.model_dump()
        doc.setdefault("stage", "Discover")
        doc["organization_id"] = organization_id
        res = self.opps.insert_one(doc)
        return str(res.inserted_id)

    def set_document_text(
        self,
        opportunity_id: str,
        organization_id: str,
        document_text: str,
        document_url: str | None = None,
    ) -> bool:
        """Store the (digested) solicitation text on an opp. Org-scoped.

        Leaves analyzed_at untouched, so a freshly-created manual opp is still picked
        up by list_unanalyzed_opportunities for the Analyst.
        """
        update: dict = {"document_text": document_text, "updated_at": _utc_now()}
        if document_url:
            update["document_url"] = document_url
        try:
            res = self.opps.update_one(
                {"_id": ObjectId(opportunity_id), "organization_id": organization_id},
                {"$set": update},
            )
        except Exception:  # noqa: BLE001 — malformed id
            return False
        return res.matched_count > 0

    def set_ingesting(
        self, opportunity_id: str, organization_id: str, value: bool, error: str | None = None
    ) -> bool:
        """Mark/clear an opportunity's ingest pipeline (parse -> digest -> Analyst) as in-flight.

        Set True at manual creation so the opp shows in the UI's "Ingesting" section. Cleared
        on success by apply_verdict (atomically with analyzed_at), or here with an `error` on a
        task's terminal failure — so a failed ingest never sits "Ingesting" forever. Org-scoped.
        """
        update: dict = {"ingesting": value}
        if error:
            update["ingest_error"] = error
        elif not value:
            update["ingest_error"] = None
        try:
            res = self.opps.update_one(
                {"_id": ObjectId(opportunity_id), "organization_id": organization_id},
                {"$set": update},
            )
        except Exception:  # noqa: BLE001 — malformed id
            return False
        return res.matched_count > 0

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
        """Human-approve an opportunity for capture (lets the capture agents run).

        Clears any previous failure in the same write, so re-approving after a failed run is
        a genuine retry — otherwise the stale `capture_failed_at` would keep the opp badged
        as failed while the new run is actually in flight.
        """
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {"capture_approved": True},
             "$unset": {"capture_failed_at": "", "capture_error": ""}},
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

    def mark_capture_failed(self, opportunity_id: str, error: str) -> None:
        """Terminal capture failure — a FIRST-CLASS state, not a fake success.

        This used to stamp `captured_at` so the opp would fall out of the UI's 'Processing'
        window (capture_approved && !captured_at). That worked, but it made a failed run
        indistinguishable from a finished one — it landed in 'Capture complete' with no
        documents. `capture_failed_at` says what actually happened, keeps the opp out of
        BOTH buckets, and lets the UI offer a retry instead of a permanent spinner.
        """
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {"capture_failed_at": _utc_now(), "capture_error": error}},
        )

    def clear_capture_failure(self, opportunity_id: str) -> None:
        """Wipe the failure so a retry starts clean (the Approve-Capture path calls this)."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$unset": {"capture_failed_at": "", "capture_error": ""}},
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
                # Structured risk — the same judgement the rationale explains in prose, kept
                # as fields so the UI can render a meter and the list can filter on it.
                "risk_level": verdict.risk_level,
                "risk_factors": [f.model_dump() for f in verdict.risk_factors],
                "stage": verdict.recommended_stage,
                "analyzed_at": _utc_now(),
                # Analyst-done is the single choke point where a manual opp leaves the
                # "Ingesting" section: it gains a verdict and the flag clears in one write.
                "ingesting": False,
                "ingest_error": None,
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

    def set_sharepoint_folder(self, opportunity_id: str, organization_id: str, folder: dict) -> bool:
        """Record the SharePoint Bid folder pointer (drive/item/web_url/subfolders) on the opp."""
        res = self.opps.update_one(
            {"_id": ObjectId(opportunity_id), "organization_id": organization_id},
            {"$set": {"sharepoint_folder": folder, "sharepoint_folder_at": _utc_now()}},
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
        object_key: str | None = None,
    ) -> str:
        """Record a generated document (a pointer — the file lives in iDrive/SharePoint).

        `object_key` (the iDrive object key) is stored when known so fresh presigned
        URLs can be re-minted directly from it, instead of parsing the (possibly stale
        or placeholder) stored URL — see routers.documents.fresh_document_url.
        """
        now = _utc_now()
        doc = {
            "opportunity_id": opportunity_id,
            "agent_id": agent_id,
            "type": doc_type,
            "title": title,
            "url": url,
            "status": status,        # draft | approved | filed
            "version": version,
            "created_at": now,
            "updated_at": now,
        }
        if object_key:
            doc["object_key"] = object_key
        res = self.documents.insert_one(doc)
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
    # NOTE: `list_all` lived here — every opportunity in the org, whole documents, no
    # projection. It was the last unprojected org-wide read, and its final caller
    # (`call_plan`) was pulling 3,499 documents carrying `document_text` to build 203 cards:
    # 50-124s against a remote cluster. Removed rather than left as a footgun, since any new
    # caller would inherit the same collapse. Read through a projection instead —
    # PLANNING_PROJECTION or CALL_PLAN_PROJECTION below, or add one.

    # Exactly what the action planner's ladder reads — and nothing else. The planner walks
    # EVERY pursuit in the org, and `list_all` returns whole documents including
    # `document_text` (the parsed solicitation, routinely tens of KB). Reading 2,500 of those
    # to look at a dozen scalar fields took minutes; projected, it is a couple of seconds.
    PLANNING_PROJECTION: dict[str, int] = {f: 1 for f in (
        "title", "agency", "response_deadline", "stage", "assigned_to", "organization_id",
        "bid_decision", "priority_score", "risk_level", "risk_factors", "ingesting",
        "capture_approved", "captured_at", "capture_failed_at", "capture_error",
        "capture_reviewed_at", "poc_name", "poc_email", "recommended_contacts",
    )}

    # Exactly what call_plan() reads to build a card, and nothing else. Same reasoning as
    # PLANNING_PROJECTION above: `recommended_contacts` is the only non-scalar, and
    # `document_text` must never be in here.
    CALL_PLAN_PROJECTION: dict[str, int] = {f: 1 for f in (
        "title", "agency", "priority_score", "bid_decision", "response_deadline",
        "poc_name", "poc_email", "captured_at", "recommended_contacts",
    )}

    def opportunities_by_id(
        self, organization_id: str, opportunity_ids: list[str], fields: tuple[str, ...]
    ) -> dict[str, dict]:
        """id -> {requested fields}, org-scoped. One query for a whole list of cards, so N
        rows never means N round trips."""
        oids = []
        for i in opportunity_ids:
            try:
                oids.append(ObjectId(i))
            except Exception:  # noqa: BLE001 — malformed id, just not found
                continue
        if not oids:
            return {}
        # Same second-round-trip trap as list_actions: this is called with one id per card,
        # so any plan over 101 pursuits paid an extra remote hop for the remainder.
        cursor = self.opps.find(
            {"_id": {"$in": oids}, "organization_id": organization_id},
            {f: 1 for f in fields},
        ).batch_size(_ONE_BATCH)
        return {str(d["_id"]): _serialize(d) for d in cursor}

    def list_for_planning(self, organization_id: str) -> list[dict]:
        """Every pursuit in the org, slim, for the daily action planner. Org-wide on purpose:
        the plan is computed for everyone and filtered by assignment when it is READ."""
        cursor = self.opps.find({"organization_id": organization_id}, self.PLANNING_PROJECTION)
        return [_serialize(d) for d in cursor]

    # NOTE: `list_all_enriched` lived here — every opportunity in the org with its
    # documents/calls/tasks attached. It was well written (batched `$in` reads, not
    # 3-per-opp) but it was still the whole org in one payload: ~10 MB / 9.5 s on a large
    # org. Removed along with its only caller, GET /api/opportunities. The list now uses
    # /page (SLIM + paged) and the detail pane loads children lazily via /{id}.

    # --- paginated + filtered reads (server-side) -------------------------
    @staticmethod
    def _status_clause(status: str) -> dict | None:
        """Mongo clause for a pipeline status bucket — mirrors the frontend FILTERS predicates
        exactly. Buckets intentionally overlap (a Bid can also be 'processing'); they're
        independent filters, not a partition. Returns None for 'all'/unknown."""
        if status in ("Bid", "Watch", "No-Bid"):
            return {"bid_decision": status}
        if status == "captured":
            return {"captured_at": {"$ne": None}}
        if status == "ingesting":
            return {"ingesting": True}
        if status == "processing":
            # Capture approved, not finished, and NOT failed — a genuinely in-flight run.
            # Without the failure clause a dead run sits here forever looking like work.
            return {"$and": [
                {"capture_approved": True},
                {"captured_at": {"$in": [None]}},
                {"$or": [{"capture_failed_at": {"$exists": False}},
                         {"capture_failed_at": None}]},
            ]}
        if status == "capture_failed":
            return {"capture_failed_at": {"$ne": None}}
        if status == "new":  # no verdict yet AND not still ingesting
            return {"$and": [
                {"$or": [{"bid_decision": {"$exists": False}}, {"bid_decision": None}]},
                {"ingesting": {"$ne": True}},
            ]}
        return None

    @staticmethod
    def _value_clause(bucket: str) -> dict | None:
        if bucket == "lt1m":
            return {"estimated_value": {"$lt": 1_000_000}}
        if bucket == "1to10m":
            return {"estimated_value": {"$gte": 1_000_000, "$lte": 10_000_000}}
        if bucket == "gt10m":
            return {"estimated_value": {"$gt": 10_000_000}}
        return None

    @staticmethod
    def _due_clause(due_days: int) -> dict:
        """Response deadline within the next `due_days` days (not past). Dates are ISO
        'YYYY-MM-DD' strings, so lexicographic comparison is chronological."""
        today = date.today().isoformat()
        cutoff = (date.today() + timedelta(days=int(due_days))).isoformat()
        return {"response_deadline": {"$gte": today, "$lte": cutoff}}

    def _filter_query(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True, *,
        status: str | None = None, agencies: list[str] | None = None, naics: list[str] | None = None,
        set_asides: list[str] | None = None, source: str | None = None,
        value_bucket: str | None = None, due_days: int | None = None,
        q: str | None = None, posted_date: str | None = None,
    ) -> dict:
        """Compose the full org-scoped, RBAC-aware, filtered Mongo query. Every filtering read
        (list page, counts base, facet dates) goes through this so counts and rows always agree.
        Clauses are AND-folded (each may carry its own $or) so multiple $or filters don't collide."""
        clauses: list[dict] = [self._visibility_query(organization_id, viewer_id, is_admin)]
        sc = self._status_clause(status) if status and status != "all" else None
        if sc:
            clauses.append(sc)
        if agencies:
            clauses.append({"agency": {"$in": agencies}})
        if naics:
            clauses.append({"naics": {"$in": naics}})
        if set_asides:
            clauses.append({"set_aside": {"$in": set_asides}})
        if source:
            clauses.append({"source": source})
        vc = self._value_clause(value_bucket) if value_bucket and value_bucket != "any" else None
        if vc:
            clauses.append(vc)
        if due_days:
            clauses.append(self._due_clause(due_days))
        if q and q.strip():
            # EVERY WORD must appear, in ANY order, across title / agency / solicitation #.
            #
            # This was a single literal substring match, so word order decided whether you
            # found anything: searching "UHF Handheld Receiver overhaul" returned nothing
            # because the record is titled "Open, Inspect, Report and Overhaul UHF Handheld
            # Receiver" — same words, rearranged. Nobody types titles in their exact order.
            #
            # Each token is AND-ed (so more words narrow the result, as expected) and within a
            # token the three fields are OR-ed (so "navy antenna" matches an antenna title at
            # a Navy agency). Tokens are escaped, so punctuation in a solicitation number is
            # literal, not a pattern.
            for token in q.strip().split():
                rx = re.escape(token)
                clauses.append({"$or": [
                    {"title": {"$regex": rx, "$options": "i"}},
                    {"agency": {"$regex": rx, "$options": "i"}},
                    {"solicitation_number": {"$regex": rx, "$options": "i"}},
                ]})
        if posted_date:
            clauses.append({"posted_date": posted_date})
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def list_page(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True, *,
        offset: int = 0, limit: int = 50, with_counts: bool = False, **filters,
    ) -> tuple[list[dict], int, dict | None]:
        """One SLIM page of opportunities, the total match count, and (optionally) the
        status pill counts — all fetched CONCURRENTLY.

        Sorted priority desc with a stable _id tiebreak (many share priority_score=null).
        No documents/calls/tasks — the detail pane loads those lazily.

        WHY THREADS. These are three independent, network-bound queries; run serially the
        page waits for the sum of them. pymongo releases the GIL while waiting on a
        socket, so a thread per query genuinely overlaps them and the page costs the
        SLOWEST query rather than all three added up. (This is the sync equivalent of a
        `Promise.all` in a Node service.) An async driver would do the same job, but this
        store is shared with nine Celery task modules that are synchronous — threads keep
        one code path for both callers.

        WHY NOT `$facet` FOR THE ROWS. It looks tempting to fetch rows+total+counts in a
        single aggregation, but a `$facet` sub-pipeline CANNOT use an index: the sort
        would become a blocking in-memory sort of every matching document. The find()
        below uses the (organization_id, priority_score, _id) index and stops after
        `limit` rows. Counting and faceting are index-friendly; sorting is not.
        """
        q = self._filter_query(organization_id, viewer_id, is_admin, **filters)
        take = max(1, min(int(limit), MAX_PAGE_SIZE))
        skip = max(0, int(offset))

        def _rows() -> list[dict]:
            cursor = (
                self.opps.find(q, SLIM_PROJECTION)
                .sort([("priority_score", -1), ("_id", -1)])
                .skip(skip)
                .limit(take)
            )
            return [_serialize(d) for d in cursor]

        with ThreadPoolExecutor(max_workers=3) as pool:
            rows_f = pool.submit(_rows)
            total_f = pool.submit(self.opps.count_documents, q)
            counts_f = (
                pool.submit(self.status_counts, organization_id, viewer_id, is_admin, **filters)
                if with_counts
                else None
            )
            rows, total = rows_f.result(), total_f.result()
            counts = counts_f.result() if counts_f else None
        return rows, total, counts

    def status_counts(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True, **filters,
    ) -> dict[str, int]:
        """Per-status pill counts for the CURRENT facet/search/date filter (but NOT the active
        status — every bucket is counted). One aggregation with $facet."""
        filters.pop("status", None)  # counts span all statuses regardless of the selected pill
        base = self._filter_query(organization_id, viewer_id, is_admin, **filters)
        facets: dict[str, list] = {"all": [{"$count": "n"}]}
        for key in ("Bid", "Watch", "No-Bid", "captured", "ingesting", "processing", "new"):
            facets[key] = [{"$match": self._status_clause(key)}, {"$count": "n"}]
        res = list(self.opps.aggregate([{"$match": base}, {"$facet": facets}]))
        row = res[0] if res else {}
        return {k: (v[0]["n"] if v else 0) for k, v in row.items()}

    def facet_values(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True,
    ) -> dict[str, list[str]]:
        """Distinct agency / NAICS / set-aside values for the dropdown options — RBAC-scoped so a
        member's options match their visible rows. Sorted, non-null."""
        base = self._visibility_query(organization_id, viewer_id, is_admin)
        out: dict[str, list[str]] = {}
        for key, field in (("agencies", "agency"), ("naics", "naics"), ("set_asides", "set_aside")):
            vals = [v for v in self.opps.distinct(field, base) if v]
            out[key] = sorted(vals, key=lambda s: s.lower())
        return out

    def posted_dates(
        self, organization_id: str, viewer_id: str | None = None, is_admin: bool = True, **filters,
    ) -> list[str]:
        """Distinct posted_date values across the active filter (minus posted_date itself) — so the
        calendar dots are correct across every page, not just the loaded one."""
        filters.pop("posted_date", None)
        q = self._filter_query(organization_id, viewer_id, is_admin, **filters)
        return sorted(d for d in self.opps.distinct("posted_date", q) if d)

    def get_opportunity_enriched(self, opportunity_id: str, organization_id: str) -> dict | None:
        """The full, heavy opportunity for the detail pane: base doc + documents/calls/tasks.
        Org-scoped (None if not found / other org). Keeps the join OFF the list endpoint."""
        opp = self.get_opportunity(opportunity_id, organization_id)
        if opp is None:
            return None
        opp["documents"] = self.list_documents(opportunity_id)
        opp["calls"] = self.list_calls(opportunity_id)
        opp["tasks"] = self.list_tasks(opportunity_id)
        return opp

    def list_calls(self, opportunity_id: str) -> list[dict]:
        cursor = self.calls.find({"opportunity_id": opportunity_id})
        return [_serialize(d) for d in cursor]

    def list_tasks(self, opportunity_id: str) -> list[dict]:
        cursor = self.tasks.find({"opportunity_id": opportunity_id})
        return [_serialize(d) for d in cursor]

    # Addresses that are machines, not people — nobody can take a call at one, so they never
    # earn a brief. Kept to unambiguous no-reply patterns ONLY. A shared/role mailbox is NOT
    # in here: `dibbsbsm@dla.mil` (the DLA DIBBS bid board) is monitored and reps genuinely
    # email it with solicitation questions. Filtering it removed the sole contact on those
    # pursuits, which hid the Prep-calls button and stranded briefs already written.
    _NO_REPLY = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply")

    @staticmethod
    def _person_name(raw: str | None) -> str | None:
        """A POC 'name' only if it plausibly IS one.

        SAM.gov routinely stuffs instructions into this field — "Questions regarding this
        solicitation should be emailed to the buyer listed in block 5… https://dibbs…". Passed
        through, that blob lands in the brief prompt several times over, costing tokens and
        telling the model a paragraph is a person. Anything sentence-shaped is dropped and the
        caller falls back to the email address.
        """
        name = " ".join((raw or "").split())
        if not name or len(name) > 60 or len(name.split()) > 5:
            return None
        if any(ch in name for ch in ("http", "@", ".com", "/")):
            return None
        return name

    @classmethod
    def call_contacts(cls, opp: dict) -> list[dict]:
        """Who a rep would call on this pursuit — the POC first, then the Relation agent's
        recommended contacts. These become the per-person tabs in the Call Plan dialog, so the
        list is de-duplicated by email and only ever includes people we can identify by address
        (the brief is built by searching the mailbox for their org's domain)."""
        out: list[dict] = []
        seen: set[str] = set()

        def _add(name, email, title=None, company=None, source="contact"):
            addr = (email or "").strip().lower()
            if not addr or "@" not in addr or addr in seen:
                return
            if any(bot in addr.split("@", 1)[0] for bot in cls._NO_REPLY):
                return
            seen.add(addr)
            out.append({"name": cls._person_name(name), "email": addr, "title": title or None,
                        "company": company or None, "source": source})

        _add(opp.get("poc_name"), opp.get("poc_email"), source="poc")
        for c in opp.get("recommended_contacts") or []:
            _add(c.get("name"), c.get("email"), c.get("title"), c.get("company"),
                 source=c.get("source") or "recommended")
        return out

    # --- consolidated call plan (across the whole pipeline) ----------------
    def call_plan(self, organization_id: str) -> list[dict]:
        """One row per PURSUIT worth calling on — the consolidated BD call sheet, sorted by
        priority (then nearest deadline).

        BID ONLY. A pursuit belongs here when its verdict is "Bid" AND something has
        happened on it — the Analyst raised a call, or capture produced deliverables.

        The verdict test is the whole point of the sheet: it is a list of customers to ring
        about work you are actually chasing. It previously admitted anything with a call
        row, so a pursuit the Analyst flagged while it looked live stayed on the sheet after
        a human ruled No-Bid (nothing closes the call row), and Watch rows sat alongside
        committed ones. On real data that was 30 of 158 rows.

        One row per OPPORTUNITY, not per call row: the card opens a dialog with a tab per
        contact, so a second call row on the same pursuit would just duplicate the card. When
        several calls exist, the newest supplies the talking point and the Done/Dismiss state.
        """
        # Read the CALLS first, then only the pursuits that can actually produce a card.
        #
        # This used to call list_all(), which returns whole documents — including
        # `document_text`, the parsed solicitation at tens of KB apiece — for every pursuit in
        # the org. On 3,499 pursuits that is a multi-hundred-megabyte read to populate 203
        # cards, and it measured 50-124s against a remote cluster. Projecting the fields fixed
        # the payload; inverting the order fixes the row count too, because a pursuit with
        # neither a call nor a capture is discarded a few lines below anyway.
        #
        # `calls` carries no organization_id of its own, so it is read unscoped and then
        # intersected with an org-scoped opportunity query — the org filter still decides what
        # can be returned, and an id from another org simply matches nothing.
        latest: dict[str, dict] = {}
        for c in self.calls.find().batch_size(_ONE_BATCH):
            sc = _serialize(c)
            oid = c["opportunity_id"]
            prev = latest.get(oid)
            if prev is None or (sc.get("created_at") or "") >= (prev.get("created_at") or ""):
                latest[oid] = sc

        called_oids = []
        for i in latest:
            try:
                called_oids.append(ObjectId(i))
            except Exception:  # noqa: BLE001 — malformed id, just not found
                continue

        q = self._visibility_query(organization_id, None, True)
        q["$or"] = [{"_id": {"$in": called_oids}}, {"captured_at": {"$ne": None}}]
        cursor = (
            self.opps.find(q, self.CALL_PLAN_PROJECTION)
            .sort("priority_score", -1)
            .batch_size(_ONE_BATCH)
        )
        opps = {o["id"]: o for o in (_serialize(d) for d in cursor)}
        if not opps:
            return []

        rows: list[dict] = []
        for oid, opp in opps.items():
            call = latest.get(oid)
            captured = bool(opp.get("captured_at"))
            if call is None and not captured:
                continue  # nothing has happened on this pursuit yet

            # BID ONLY — no exceptions, capture included.
            #
            # Deliberately stricter than the action planner's post-capture rule. There a
            # call survives a passed deadline because the pursuit was committed to and the
            # work was done. Here the question is different: this sheet answers "who am I
            # ringing about the business we are chasing", and a pursuit we declined, or have
            # not decided on, is not that — whatever was produced for it earlier. A Watch
            # with a named contact is a DECISION to make; it belongs on Today as a `decide`
            # action, not on the call sheet as though it were committed work.
            if opp.get("bid_decision") != "Bid":
                continue
            rows.append({
                # None for a captured pursuit the Analyst never raised a call for — the card
                # still preps calls, it just has no call row to mark Done/Dismiss.
                "call_id": call["id"] if call else None,
                "opportunity_id": oid,
                "opportunity_title": opp.get("title"),
                "agency": opp.get("agency"),
                "priority_score": opp.get("priority_score"),
                "bid_decision": opp.get("bid_decision"),
                "response_deadline": opp.get("response_deadline"),
                # Sanitised: SAM.gov stuffs instruction paragraphs into poc_name, which used
                # to render as the card's contact line.
                "poc_name": self._person_name(opp.get("poc_name")),
                "poc_email": opp.get("poc_email"),
                "name": (call or {}).get("name"),
                "talking_point": (call or {}).get("talking_point"),
                "status": (call or {}).get("status") or "Planned",
                "created_at": (call or {}).get("created_at") or opp.get("captured_at"),
                "captured": captured,
                # The dialog's tabs: everyone worth calling on this pursuit.
                "contacts": self.call_contacts(opp),
            })
        rows.sort(
            key=lambda r: (r.get("priority_score") or -1, r.get("response_deadline") or "9999"),
            reverse=True,
        )
        return rows

    # --- call briefs (per-contact meeting prep) ---------------------------
    # One brief per (opportunity, contact, rep): the Call Plan dialog has a tab per person,
    # and each tab is its own brief. Keyed to the rep because it is built from THEIR mailbox.
    def upsert_call_brief(
        self, organization_id: str, opportunity_id: str, contact_email: str,
        employee_email: str, org_domain: str, brief: dict, *, mail_count: int = 0,
    ) -> None:
        key = {
            "organization_id": organization_id,
            "opportunity_id": opportunity_id,
            "contact_email": (contact_email or "").strip().lower(),
            "employee_email": (employee_email or "").strip().lower(),
        }
        self.call_briefs.update_one(
            key,
            {"$set": {**key, "org_domain": org_domain, "brief": brief,
                      "mail_count": int(mail_count), "refreshed_at": _utc_now()}},
            upsert=True,
        )

    def list_call_briefs(
        self, organization_id: str, opportunity_id: str, employee_email: str,
    ) -> list[dict]:
        """Every contact-brief this rep holds for one opportunity — the dialog's tabs."""
        cursor = self.call_briefs.find({
            "organization_id": organization_id,
            "opportunity_id": opportunity_id,
            "employee_email": (employee_email or "").strip().lower(),
        })
        return [_serialize(d) for d in cursor]

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

    # --- mail triage (new-mail notifications, human draft-reply loop) -----
    def find_active_bid_by_contact(self, organization_id: str, sender_email: str) -> dict | None:
        """The relevance filter: is `sender_email` a KNOWN CONTACT on any ACTIVE Bid in
        this org? Checked in Python (not a Mongo query) so matching is case-insensitive
        regardless of how the CRM agent stored the contact's email. Returns the matching
        opportunity, or None — meaning the mail isn't tied to a deal and should be dropped,
        not surfaced.

        Runs once per incoming webhook email, scanning every Bid-decision opp's
        recommended_contacts (no index can serve a case-insensitive match inside an array of
        subdocuments). Fine at today's scale (a few dozen active Bids); if an org's active-Bid
        volume grows large enough for this to matter, replace with a dedicated
        contact_email -> opportunity_id lookup collection kept in sync with recommended_contacts."""
        sender = (sender_email or "").strip().lower()
        if not sender:
            return None
        cursor = self.opps.find(
            {"organization_id": organization_id, "bid_decision": "Bid"},
            {"recommended_contacts": 1, "title": 1, "solicitation_number": 1},
        )
        for opp in cursor:
            for c in opp.get("recommended_contacts") or []:
                if (c.get("email") or "").strip().lower() == sender:
                    return _serialize(opp)
        return None

    def create_mail_triage_card(
        self, organization_id: str, employee_email: str, opportunity_id: str,
        message_id: str, sender_email: str, sender_name: str | None, subject: str,
        snippet: str, received_at: str | None, conversation_id: str | None,
        web_link: str | None = None,
    ) -> str | None:
        """Record one relevant incoming mail as a triage card. Idempotent on
        (employee_email, message_id) via the unique index — a re-delivered webhook event
        never duplicates the card. Returns the new card id, or None if already triaged."""
        try:
            res = self.mail_triage.insert_one({
                "organization_id": organization_id,
                "employee_email": employee_email,
                "opportunity_id": opportunity_id,
                "message_id": message_id,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "subject": subject,
                "snippet": snippet,
                "received_at": received_at,
                "conversation_id": conversation_id,
                "web_link": web_link,     # opens the original mail in Outlook
                "status": "unread",       # unread | read | dismissed | replied
                "suggested_reply": None,  # filled in by the draft-reply task
                "reply_error": None,      # set if reply generation exhausts its retries
                "created_at": _utc_now(),
            })
            return str(res.inserted_id)
        except DuplicateKeyError:
            return None  # already triaged this message for this employee

    def list_mail_triage(self, organization_id: str, employee_email: str) -> list[dict]:
        """This employee's triage cards (their own inbox only), newest first. Excludes
        dismissed cards so the Dashboard only shows what still needs attention."""
        cursor = self.mail_triage.find({
            "organization_id": organization_id,
            "employee_email": employee_email,
            "status": {"$ne": "dismissed"},
        }).sort("created_at", -1)
        return [_serialize(d) for d in cursor]

    def list_open_mail_triage(self, organization_id: str) -> list[dict]:
        """Every still-unanswered triage card in the org, across all mailboxes — the planner's
        input for `reply_mail` actions. `replied` is excluded as well as `dismissed`: once a
        draft has gone into Outlook the ball is no longer in our court."""
        cursor = self.mail_triage.find({
            "organization_id": organization_id,
            "status": {"$nin": ["dismissed", "replied"]},
        })
        return [_serialize(d) for d in cursor]

    def get_mail_triage_card(self, card_id: str, employee_email: str) -> dict | None:
        """One card, scoped to the requesting employee (their own inbox only)."""
        try:
            doc = self.mail_triage.find_one({"_id": ObjectId(card_id), "employee_email": employee_email})
        except Exception:  # noqa: BLE001 — malformed id
            return None
        return _serialize(doc) if doc else None

    def update_mail_triage_card(self, card_id: str, employee_email: str, **fields) -> bool:
        """Update a card's mutable fields (status, suggested_reply, ...) — scoped to the
        owning employee so one person can never touch another's triage queue."""
        fields["updated_at"] = _utc_now()
        try:
            res = self.mail_triage.update_one(
                {"_id": ObjectId(card_id), "employee_email": employee_email}, {"$set": fields},
            )
        except Exception:  # noqa: BLE001 — malformed id
            return False
        return res.matched_count > 0

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

    # --- daily action plan ------------------------------------------------
    # One row per thing a HUMAN must do, on a given day. The planner
    # (tasks/action_plan_tasks.py) writes them; the Today view reads them. See
    # models/action.py and docs/daily-action-plan.md.

    # Set by the planner on every run — the facts these describe can change day to day.
    # `due_on` is handled separately because a human may have overridden it (see below).
    _ACTION_PLANNER_FIELDS = (
        "title", "reason", "hard_deadline", "urgency", "infeasible", "assigned_to", "ref_id",
    )

    def _action_update(self, action: dict) -> list[dict]:
        """The aggregation-pipeline update for one planned action. See `upsert_actions`."""
        now = _utc_now()
        sets: dict = {k: action.get(k) for k in self._ACTION_PLANNER_FIELDS}
        sets.update({
            # Identity — constant for a given dedupe_key, cheap to keep re-asserting.
            "organization_id": action["organization_id"],
            "kind": action["kind"],
            "opportunity_id": action.get("opportunity_id"),
            "contact_email": action.get("contact_email"),
            "updated_at": now,
            # Insert-only, emulated: on an upsert-insert the field does not exist yet, so
            # $ifNull yields the default; on a re-run it yields whatever is already there.
            # The one exception is an AUTO-completed row: the planner closed it because the
            # state said the step was satisfied, so if the planner is asking for it again the
            # state has regressed and it belongs back on the list.
            "status": {"$cond": [
                {"$and": [{"$eq": ["$status", "done"]}, {"$eq": ["$auto_completed", True]}]},
                "open",
                {"$ifNull": ["$status", "open"]},
            ]},
            "created_at": {"$ifNull": ["$created_at", now]},
            "due_on": {
                "$cond": [{"$eq": ["$user_scheduled", True]}, "$due_on", action["due_on"]],
            },
        })
        return [{"$set": sets}]

    def upsert_actions(self, actions: list[dict]) -> int:
        """Write MANY planned actions in ONE round trip.

        The planner writes a couple of hundred of these per org per run. Issued one at a time
        against a remote database that was ~0.6 s each — over two minutes for a job whose
        actual work is milliseconds. A single `bulk_write` makes the whole batch one trip.

        `ordered=False` so one bad row cannot abandon the rest of the day's plan.
        """
        if not actions:
            return 0
        ops = [
            UpdateOne({"dedupe_key": a["dedupe_key"]}, self._action_update(a), upsert=True)
            for a in actions
        ]
        res = self.actions.bulk_write(ops, ordered=False)
        return res.upserted_count + res.modified_count

    def upsert_action(self, action: dict) -> None:
        """Write one planned action, idempotently on `dedupe_key`.

        The planner runs daily AND after every pipeline event, so this is called many times
        for the same action. Two rules keep that safe:

        * **A human's close-out is final; the system's is not.** A `dismissed` action, or one
          a person ticked off by hand, stays closed forever — the planner cannot nag someone
          about a decision they already made. But one the planner itself auto-closed reopens
          if the state that satisfied it regresses. That is what stops a second capture
          failure, after a first was fixed, from vanishing silently.
        * **`due_on` is not overwritten once a human has scheduled it.** Snoozing sets
          `user_scheduled`; after that the day belongs to the rep, not the ladder. Urgency and
          the reason line still refresh, because those are facts about the deadline and they
          genuinely change.

        A pipeline update (rather than $set/$setOnInsert) because that conditional on `due_on`
        cannot be expressed in a plain update document.
        """
        self.actions.update_one(
            {"dedupe_key": action["dedupe_key"]}, self._action_update(action), upsert=True,
        )

    @staticmethod
    def _action_visibility(viewer_id: str | None, scope: str) -> dict:
        """Whose actions. `scope="mine"` applies to admins too — "*your* tasks for today" is
        the whole premise, so an admin has to ASK for the org-wide list."""
        if scope == "org" or not viewer_id:
            return {}
        return {"$or": [
            {"assigned_to": viewer_id},            # array contains the viewer
            {"assigned_to": {"$in": [None, []]}},  # explicitly unassigned
            {"assigned_to": {"$exists": False}},
        ]}

    def list_actions(
        self, organization_id: str, *, viewer_id: str | None = None, scope: str = "mine",
        horizon_days: int = 7,
    ) -> list[dict]:
        """Every OPEN action due within `horizon_days` (plus everything overdue).

        No status filter beyond `open`: done/dismissed/expired rows stay in the collection as
        history, they just never come back to the list.
        """
        cutoff = (_utc_today() + timedelta(days=int(horizon_days))).isoformat()
        q: dict = {
            "organization_id": organization_id,
            "status": "open",
            "due_on": {"$lte": cutoff},  # ISO dates sort lexicographically = chronologically
        }
        q.update(self._action_visibility(viewer_id, scope))
        # batch_size, because the driver's default first batch is 101 documents and a real
        # day's plan is comfortably more than that: at 113 rows the cursor made a second
        # round trip for the last twelve. Against a remote cluster one round trip is ~1.3s,
        # so that tail cost more than the query — measured 2.3s -> 1.0s for the same 113 rows.
        # Actions are ~540 bytes each; a thousand of them still fit one batch easily.
        return [_serialize(d) for d in self.actions.find(q).batch_size(_ONE_BATCH)]

    def set_action_status(
        self, action_id: str, organization_id: str, status: str, *, snooze_days: int = 1,
    ) -> dict | None:
        """Human close-out: done / dismissed / snoozed. Org-scoped. Returns the updated row.

        Snoozing stamps `user_scheduled` so the next planner run leaves the new day alone —
        without it the ladder would recompute `due_on` and drag the card straight back to
        today, which would make the snooze button a lie.
        """
        fields: dict = {"status": status, "updated_at": _utc_now()}
        if status == "snoozed":
            day = (_utc_today() + timedelta(days=max(1, int(snooze_days)))).isoformat()
            fields.update({"snoozed_to": day, "due_on": day, "user_scheduled": True})
        elif status in ("done", "dismissed"):
            fields.update({"completed_at": _utc_now(), "auto_completed": False})
        try:
            doc = self.actions.find_one_and_update(
                {"_id": ObjectId(action_id), "organization_id": organization_id},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        except Exception:  # noqa: BLE001 — malformed id
            return None
        return _serialize(doc) if doc else None

    def close_actions(
        self, organization_id: str, opportunity_ids: list[str], *, status: str = "done",
        kinds: list[str] | None = None,
    ) -> int:
        """Close open actions across MANY pursuits at once — the planner's auto-completion
        and expiry path.

        `status="done"` with `auto_completed=True` is what makes the list honest: approve
        capture from the Pipeline and the matching card disappears on its own, so nobody ever
        ticks the same thing off twice. `status="expired"` is the pursuit going terminal, kept
        distinct from `dismissed` so "he decided not to" and "it stopped mattering" never look
        the same in the history.

        Takes a LIST because the planner sweeps a whole org: one update_many per outcome
        rather than one per opportunity, which on a few thousand pursuits is the difference
        between nine writes and several thousand.
        """
        if not opportunity_ids:
            return 0
        q: dict = {
            "organization_id": organization_id,
            "opportunity_id": {"$in": opportunity_ids},
            "status": {"$in": ["open", "snoozed"]},
        }
        if kinds is not None:
            if not kinds:
                return 0
            q["kind"] = {"$in": kinds}
        res = self.actions.update_many(q, {"$set": {
            "status": status, "completed_at": _utc_now(),
            "auto_completed": status == "done", "updated_at": _utc_now(),
        }})
        return res.modified_count

    def closed_action_kinds(self, organization_id: str) -> dict[str, set[str]]:
        """opportunity_id -> the steps a HUMAN has closed on it (dismissed, or ticked off by
        hand). One query for the whole org; the planner needs it for every pursuit.

        The ladder skips these. Without it, a step with no state field of its own to check
        (`review_docs`) would be recreated forever and the pursuit could never reach `submit`;
        and dismissing a call the rep has decided not to make would just bring it back
        tomorrow. Auto-completed rows are deliberately NOT here — those are the system's own
        bookkeeping, and for them the state predicate is the truth.
        """
        out: dict[str, set[str]] = {}
        cursor = self.actions.find(
            {"organization_id": organization_id,
             "$or": [{"status": "dismissed"},
                     {"status": "done", "auto_completed": {"$ne": True}}]},
            {"kind": 1, "opportunity_id": 1},
        )
        for d in cursor:
            if d.get("opportunity_id"):
                out.setdefault(d["opportunity_id"], set()).add(d["kind"])
        return out

    def calls_by_opportunity(self, opportunity_ids: list[str]) -> dict[str, list[dict]]:
        """All calls for many pursuits, grouped — one query instead of N."""
        out: dict[str, list[dict]] = {}
        if not opportunity_ids:
            return out
        for d in self.calls.find({"opportunity_id": {"$in": opportunity_ids}}):
            out.setdefault(d["opportunity_id"], []).append(_serialize(d))
        return out

    def document_counts(self, opportunity_ids: list[str]) -> dict[str, int]:
        """How many documents each pursuit has. A count, not the documents — the planner only
        needs to know whether capture produced anything to review."""
        out: dict[str, int] = {}
        if not opportunity_ids:
            return out
        for d in self.documents.aggregate([
            {"$match": {"opportunity_id": {"$in": opportunity_ids}}},
            {"$group": {"_id": "$opportunity_id", "n": {"$sum": 1}}},
        ]):
            out[d["_id"]] = d["n"]
        return out

    def mark_capture_reviewed(self, opportunity_id: str) -> None:
        """A human has looked at the capture documents. Satisfies the `review_docs` step and
        unblocks `submit` — without this the ladder would stall there."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)}, {"$set": {"capture_reviewed_at": _utc_now()}}
        )

    def unsnooze_due_actions(self, organization_id: str) -> int:
        """Snoozed actions whose day has arrived come back onto the list."""
        res = self.actions.update_many(
            {"organization_id": organization_id, "status": "snoozed",
             "snoozed_to": {"$lte": _utc_today().isoformat()}},
            {"$set": {"status": "open", "updated_at": _utc_now()}},
        )
        return res.modified_count


_store: CRMStore | None = None
_lock = threading.RLock()


def get_crm_store() -> CRMStore:
    global _store
    with _lock:
        if _store is None:
            _store = CRMStore()
        return _store
