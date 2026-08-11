"""Today — the daily action plan.

Reads the rows the planner (tasks/action_plan_tasks.py) wrote and hands back ONE payload the
Today view renders without a second fetch: what is overdue, what is due today, and a peek at
the rest of the week.

WHY THE BUCKETING HAPPENS HERE and not in the browser. "Today" is a UTC day — that is the day
boundary the planner schedules against, and the boss's team is not all in one timezone. Left
to the client, a rep in IST opens the app and everything the planner scheduled for today reads
as overdue, because their local date is already tomorrow.

Spec: docs/daily-action-plan.md
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import get_current_user
from client.crm_store import get_crm_store

# Imported at module scope, not inside _decorate. As a function-local it ran on EVERY
# request — and because it is the first thing to pull in the task package, the first request
# after a worker boot paid 1.4s of import before it did any work. It is a pure string helper;
# there is no cycle to avoid here.
from tasks.action_plan_tasks import _agency_label

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/actions", tags=["actions"])

# Ordering: the loudest thing first, then whatever runs out of road soonest, then the
# Analyst's own ranking. Everything after the first key is a tiebreak.
_URGENCY_RANK = {"critical": 0, "high": 1, "normal": 2}

# How far past today the "coming up" strip looks. A week: far enough to see the shape of the
# next few days, near enough that it is still this week's problem.
UPCOMING_DAYS = 7


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@router.get("/today")
def todays_actions(
    scope: str = Query("mine", pattern="^(mine|org)$"),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """This person's work for today: overdue, due today, and a peek at the week.

    `scope=org` (admins only) widens it to everyone's — deliberately opt-in, because "*your*
    tasks for today" is the entire premise and an admin defaulting to the org-wide list would
    just rebuild the firehose.
    """
    org = str(current_user["organization_id"])
    is_admin = current_user.get("role") == "admin"
    if scope == "org" and not is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can see the whole org's plan.")

    crm = get_crm_store()
    rows = crm.list_actions(
        org, viewer_id=str(current_user["_id"]), scope=scope, horizon_days=UPCOMING_DAYS,
    )
    rows = _decorate(crm, org, rows)
    rows.sort(key=lambda r: (
        _URGENCY_RANK.get(r.get("urgency"), 3),
        r.get("hard_deadline") or "9999-12-31",
        -(r.get("priority_score") or 0),
    ))

    today = _today()
    overdue = [r for r in rows if r["due_on"] < today]
    due_today = [r for r in rows if r["due_on"] == today]
    later = [r for r in rows if r["due_on"] > today]

    return {
        "date": today,
        "scope": scope,
        "overdue": overdue,
        "today": due_today,
        # Collapsed to a count per day, NOT the cards. Today's page must not quietly contain
        # next week's work — that is the thing this view exists to stop.
        "upcoming": _by_day(later),
        "counts": {
            "overdue": len(overdue),
            "today": len(due_today),
            "upcoming": len(later),
            "critical": sum(1 for r in overdue + due_today if r.get("urgency") == "critical"),
        },
    }


def _by_day(rows: list[dict]) -> list[dict]:
    """[{day, count}] for the coming-up strip, in date order."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["due_on"]] = counts.get(r["due_on"], 0) + 1
    return [{"day": d, "count": counts[d]} for d in sorted(counts)]


# What a card needs from its opportunity to render. Fetched in ONE query for the whole list —
# a card is useless without the pursuit's name, and N cards must not mean N round trips.
_OPP_FIELDS = ("title", "agency", "solicitation_number", "priority_score", "bid_decision",
               "risk_level", "response_deadline", "stage", "captured_at", "link")


def _decorate(crm, organization_id: str, rows: list[dict]) -> list[dict]:
    """Fold each action's opportunity onto it, so the client renders straight from this."""
    ids = {r["opportunity_id"] for r in rows if r.get("opportunity_id")}
    opps = crm.opportunities_by_id(organization_id, list(ids), _OPP_FIELDS)
    out = []
    for r in rows:
        opp = opps.get(r.get("opportunity_id") or "") or {}
        r["opportunity_title"] = opp.get("title")
        # Both: the short label is what a card shows, the full breadcrumb is what the detail
        # pane and any search would want. Shortened HERE, with the same function the planner
        # uses for titles, so a card can never disagree with its own headline.
        r["agency"] = _agency_label(opp)
        r["agency_full"] = opp.get("agency")
        r["solicitation_number"] = opp.get("solicitation_number")
        r["priority_score"] = opp.get("priority_score")
        r["bid_decision"] = opp.get("bid_decision")
        r["risk_level"] = opp.get("risk_level")
        r["link"] = opp.get("link")
        # Days to the deadline, computed HERE against the same UTC day the planner scheduled
        # against. Left to the browser it disagrees with the card's own reason line — a rep in
        # IST saw "Overdue" on a card that said "this closes today", because their local date
        # had already rolled over. One number, one source.
        r["closes_in_days"] = _days_to(r.get("hard_deadline"))
        out.append(r)
    return out


def _days_to(deadline: str | None) -> int | None:
    if not deadline:
        return None
    try:
        due = datetime.strptime(deadline[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (due - datetime.now(timezone.utc).date()).days


# --- close-out -------------------------------------------------------------------------


class SnoozeRequest(BaseModel):
    days: int = 1


def _set(action_id: str, current_user: dict, status: str, **kw) -> dict:
    action = get_crm_store().set_action_status(
        action_id, str(current_user["organization_id"]), status, **kw
    )
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


@router.post("/{action_id}/done")
def mark_done(action_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Tick it off.

    Where the step has a real field behind it, ticking it off writes that field too — a
    reviewed capture pack has to be recorded ON the pursuit, not just on the card, or the
    ladder would recreate the same task tomorrow and never advance to `submit`.
    """
    action = _set(action_id, current_user, "done")
    if action.get("kind") == "review_docs" and action.get("opportunity_id"):
        get_crm_store().mark_capture_reviewed(action["opportunity_id"])
    return action


@router.post("/{action_id}/snooze")
def snooze(
    action_id: str, req: SnoozeRequest, current_user: dict = Depends(get_current_user)
) -> dict:
    """Push it to a later day. The planner leaves that day alone afterwards."""
    if not 1 <= req.days <= 30:
        raise HTTPException(status_code=400, detail="Snooze between 1 and 30 days.")
    return _set(action_id, current_user, "snoozed", snooze_days=req.days)


@router.post("/{action_id}/dismiss")
def dismiss(action_id: str, current_user: dict = Depends(get_current_user)) -> dict:
    """Not doing this. Final — the planner never raises this step on this pursuit again."""
    return _set(action_id, current_user, "dismissed")


@router.post("/replan")
def replan(current_user: dict = Depends(get_current_user)) -> dict:
    """Rebuild this org's plan now, instead of waiting for the daily run."""
    from tasks.action_plan_tasks import plan_org_actions

    task = plan_org_actions.delay(str(current_user["organization_id"]))
    return {"queued": True, "task_id": task.id}
