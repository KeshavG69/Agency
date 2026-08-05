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
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

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
        # Serve the paginated list's sort (priority desc) with a stable _id tiebreak
        # (many opps share priority_score=null), and the member-visibility assigned_to filter.
        self.opps.create_index([("organization_id", 1), ("priority_score", -1), ("_id", -1)])
        self.opps.create_index([("organization_id", 1), ("assigned_to", 1)])
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

    def mark_capture_failed(self, opportunity_id: str, error: str) -> None:
        """Terminal capture failure — stamp captured_at (so the opp leaves the UI's
        'Processing' window, which is capture_approved && !captured_at) plus a capture_error,
        instead of sitting capture_approved forever after retries exhaust."""
        self.opps.update_one(
            {"_id": ObjectId(opportunity_id)},
            {"$set": {"captured_at": _utc_now(), "capture_error": error}},
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
            return {"$and": [{"capture_approved": True}, {"captured_at": {"$in": [None]}}]}
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
            rx = re.escape(q.strip())
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


_store: CRMStore | None = None
_lock = threading.RLock()


def get_crm_store() -> CRMStore:
    global _store
    with _lock:
        if _store is None:
            _store = CRMStore()
        return _store
