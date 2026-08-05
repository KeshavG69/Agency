"""Intelligence router — what the agents learned, and the one action a human takes on it.

Three collections were filling up with nothing able to read them. This is their read
surface, plus the single mutation that matters: a rep accepting or dismissing a
suggestion. Until that exists the enrichment loop is half-open — the system can propose
a job title forever and no human can ever settle it.

SCOPING. Every route derives `organization_id` from the JWT and never accepts it from
the client. That is the whole multi-tenancy story for this router: a rep can only ever
read and decide facts belonging to their own organisation.

INTELLIGENCE NEVER LIVES HERE. These handlers validate, call a store, and return. No
enrichment, no scoring, no vendor calls — that all belongs to the Celery/agent layer.
The one exception is `decide_fact`, which is a HUMAN decision, and human decisions are
exactly what an API is for.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.events_store import get_events_store
from client.facts_store import get_facts_store
from client.task_store import get_task_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


def _org(current_user: dict) -> str:
    org = str(current_user.get("organization_id") or "").strip()
    if not org:
        raise HTTPException(status_code=403, detail="No organization on this account")
    return org


# --- what we know about a contact --------------------------------------------------


@router.get("/contacts/{email}/facts")
def contact_facts(email: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Everything we hold on one contact: settled facts, and the open questions.

    `facts` are safe to render as truth. `suggestions` are NOT — each carries the
    evidence behind it and is waiting for a human to accept or dismiss.
    """
    org = _org(current_user)
    store = get_facts_store()
    try:
        return {
            "email": email.strip().lower(),
            "facts": store.applied_facts(org, email),
            "suggestions": store.suggestions(org, email),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Reading contact facts failed for %s: %s", email, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not read contact facts")


class DecideRequest(BaseModel):
    accept: bool


@router.post("/facts/{fact_id}/decide")
def decide_fact(
    fact_id: str, req: DecideRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Accept or dismiss one suggestion. THE human action in this whole system.

    Accepting marks the value human-owned, after which no source — however strong —
    may overwrite it. Dismissing is permanent for that value: it is never offered again.
    Both are enforced in `facts_store`, not here.
    """
    org = _org(current_user)
    updated = get_facts_store().decide_fact(
        org, fact_id, accept=req.accept, user_email=current_user["email"].lower()
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"fact": updated, "decided": "accepted" if req.accept else "dismissed"}


MAX_PAGE_SIZE = 100


@router.get("/suggestions")
def pending_suggestions(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """The org's open suggestions, strongest first — the review queue for a rep.

    Highest-confidence first on purpose: those are the ones a human can confirm at a
    glance, so the list gets shorter fastest.

    Paged, and the sort is index-backed: the `(organization_id, status, score)` index
    means Mongo walks the index in score order and stops after `limit`. Without that
    third key it would have to load every PROPOSED row in the org and sort them in
    memory — fine at fifty suggestions, a cliff at fifty thousand, which is exactly the
    scale a sweep over every mailbox produces.
    """
    org = _org(current_user)
    store = get_facts_store()
    query = {"organization_id": org, "status": "PROPOSED"}

    def _rows() -> list[dict]:
        cursor = (
            store.facts.find(query).sort([("score", -1), ("_id", -1)]).skip(offset).limit(limit)
        )
        out = []
        for r in cursor:
            r["id"] = str(r.pop("_id"))
            r.pop("organization_id", None)  # never echo the tenant key back to a client
            out.append(r)
        return out

    # Rows and the total, concurrently — the page costs the slower of the two, not both.
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows_f = pool.submit(_rows)
        total_f = pool.submit(store.facts.count_documents, query)
        rows, total = rows_f.result(), total_f.result()

    return {"suggestions": rows, "count": len(rows), "total": total,
            "offset": offset, "limit": limit}


# --- what the agents did ------------------------------------------------------------


@router.get("/events/{subject_id}")
def agent_trail(
    subject_id: str,
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Everything the agents did to one record, oldest first.

    `ok: false` marks a rejection or a miss — a No-Bid, a contact search that found
    nobody, a company that could not be identified. Those entries carry the reason, and
    they are the ones people actually come looking for.
    """
    org = _org(current_user)
    try:
        return {"subject_id": subject_id, "events": get_events_store().trail(org, subject_id, limit)}
    except Exception as exc:  # noqa: BLE001
        logger.error("Reading the agent trail failed for %s: %s", subject_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not read the agent trail")


# --- is the background work healthy? ------------------------------------------------


@router.get("/tasks/health")
def queue_health(current_user: dict = Depends(get_current_user)) -> dict:
    """Queue depth for this org — open work, what is due now, and what gave up.

    Without this the background queue is invisible: a stuck tick or a run of failures
    looks exactly like "nothing needed doing".
    """
    org = _org(current_user)
    tasks = get_task_store().tasks
    now = datetime.now(timezone.utc)
    try:
        # ONE aggregation instead of five round trips. `$facet` runs every branch over the
        # same already-matched set, so the org filter is applied once and can use its
        # index. (Safe here precisely because no branch SORTS — a $facet sub-pipeline
        # cannot use an index, which is why the paged list above is NOT built this way.)
        pipeline = [
            {"$match": {"organization_id": org}},
            {"$facet": {
                "open": [{"$match": {"finished_at": None}}, {"$count": "n"}],
                "due_now": [
                    {"$match": {"finished_at": None, "due_at": {"$lte": now}}},
                    {"$count": "n"},
                ],
                "gave_up": [
                    {"$match": {"outcome": {"$regex": "^Gave up"}}}, {"$count": "n"},
                ],
                "by_kind": [
                    {"$match": {"finished_at": None}},
                    {"$group": {"_id": "$kind", "n": {"$sum": 1}}},
                ],
                "next_due": [
                    {"$match": {"finished_at": None}},
                    {"$sort": {"due_at": 1}}, {"$limit": 1},
                    {"$project": {"_id": 0, "due_at": 1}},
                ],
            }},
        ]
        res = list(tasks.aggregate(pipeline))
        facet = res[0] if res else {}

        def _n(key: str) -> int:
            branch = facet.get(key) or []
            return int(branch[0]["n"]) if branch else 0

        next_due = facet.get("next_due") or []
        return {
            "open": _n("open"),
            "due_now": _n("due_now"),
            "gave_up": _n("gave_up"),
            "open_by_kind": {r["_id"]: r["n"] for r in (facet.get("by_kind") or [])},
            "next_due": (
                next_due[0]["due_at"].isoformat()
                if next_due and next_due[0].get("due_at") else None
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Queue health failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not read queue health")
