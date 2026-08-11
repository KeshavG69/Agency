"""The daily action plan — turning the pipeline into a to-do list.

WHAT THIS DOES. For every live pursuit it works out the ONE next thing a human owes it, and
schedules that thing BACKWARDS from the response deadline. A bid closing in three days
compresses its whole remaining chain onto today; one closing in a month contributes only its
first step, on the day that step's runway starts. Same pursuit, same steps — the deadline
decides which day each lands on and how loud it is.

WHY THERE IS NO MODEL HERE. The judgement was already made: the Analyst decided bid/no-bid,
priority and risk; the Relation agent picked the contacts. All that is left is "what is the
next step, and when is it due" — a state machine over fields we already store. An LLM here
would be slow, cost money per opportunity per day, and give two different answers on two
days for the same unchanged data. This is arithmetic; it should behave like arithmetic.

WHY IT IS SAFE TO RUN CONSTANTLY. Every write is an upsert on `dedupe_key`, and the planner
never overwrites a human's decision (see crm_store.upsert_action). So the beat run and the
half-dozen event-driven runs cannot duplicate, resurrect, or reschedule anything a person
has already dealt with.

Spec: docs/daily-action-plan.md
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from app.worker import celery_app
from auth.database import get_mongodb_client
from client.crm_store import get_crm_store
from models.action import dedupe_key

logger = logging.getLogger(__name__)

# How far ahead we bother writing rows. Beyond this a pursuit's next step is simply
# recomputed on the day it matters — there is no value in a row that says "in 40 days".
HORIZON_DAYS = 30

# A pursuit in one of these states owes nobody anything. Its open actions are expired.
TERMINAL_DECISIONS = ("No-Bid",)
TERMINAL_STAGES = ("Won", "Lost", "Submitted", "No-Bid")

# The priority a deferred ("Watch") pursuit must reach before it costs a human a decision.
#
# WHY THIS EXISTS. Watch is the ANALYST deferring, not a request for a human ruling — and the
# agent queue already schedules a `recheck_opportunity` for every one of them. Without a floor,
# every deferral becomes a daily card: on Nexagen's real data that was 204 of them, against 8
# live Bids. That is the exact firehose this whole feature exists to remove, rebuilt inside the
# feature. The floor keeps the deferrals a person would actually want to overrule and lets the
# recheck quietly handle the rest.
DECIDE_PRIORITY_FLOOR = 55


def _utc_today() -> date:
    """Today in UTC. Never `date.today()` — that is the server's local date and can be a day
    ahead, which in a system scheduled in whole days moves every task by one."""
    return datetime.now(timezone.utc).date()


def _parse_day(value) -> Optional[date]:
    """A stored 'YYYY-MM-DD' (or datetime) as a date. None when absent or unparseable —
    a malformed deadline must degrade to 'no deadline', never crash the whole org's plan."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------------------
# Every pursuit walks the same chain. At any moment exactly one step is outstanding, and
# THAT is the action. `lead_days` is the RUNWAY the step needs — how many days before the
# deadline it should be finished by — not how long it takes. It is cumulative, not additive:
# `analyze` at 21 means "an unread bid closing in three weeks is due today", because 21 days
# is what the whole remaining chain needs, not what reading it needs.


@dataclass(frozen=True)
class Step:
    kind: str
    lead_days: int
    # Days-left below which this step's remaining chain genuinely cannot be delivered. 0 =
    # never infeasible: a call or a submission with two days left is urgent, not impossible.
    min_runway: int
    pending: Callable[[dict, dict], bool]
    title: Callable[[dict, dict], str]
    reason: Callable[[dict, dict], str]


def _name(opp: dict) -> str:
    return (opp.get("title") or "this opportunity").strip()


def _short(opp: dict, limit: int = 60) -> str:
    """The title, trimmed for a card headline. Solicitation titles run to 120+ characters."""
    t = _name(opp)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _agency_label(opp: dict) -> str:
    """A recognisable agency name for a card headline.

    SAM.gov stores a full breadcrumb — "HOMELAND SECURITY, DEPARTMENT OF › U.S. COAST GUARD ›
    SFLC PROCUREMENT BRANCH 1(00080)" — which is unreadable in a title. The SECOND segment is
    the one a person recognises: the department alone is too broad, the office code too
    obscure. The trailing "(00080)" is an internal office id, never useful here.
    """
    parts = [p.strip() for p in (opp.get("agency") or "").split("›") if p.strip()]
    if not parts:
        return ""
    label = parts[1] if len(parts) > 1 else parts[0]
    return re.sub(r"\s*\(\d+\)\s*$", "", label)


def _at_agency(opp: dict) -> str:
    label = _agency_label(opp)
    return f" at {label}" if label else ""


def _person(name: str) -> str:
    """SAM.gov shouts every POC name in caps. Softened only when it IS all-caps, so a name
    with real internal capitals ("McBride", "DeLuca") is never mangled."""
    return name.title() if name.isupper() else name


def _verdict_line(opp: dict) -> str:
    """The Analyst's own words, compressed to one line — the rep should not have to open the
    record to know why this is in front of them."""
    bits = []
    if opp.get("priority_score") is not None:
        bits.append(f"P{opp['priority_score']}")
    if opp.get("risk_level"):
        bits.append(f"{opp['risk_level'].lower()} risk")
    blockers = [f for f in (opp.get("risk_factors") or []) if f.get("severity") == "blocker"]
    if blockers:
        bits.append(f"blocker: {blockers[0].get('note') or 'see the analysis'}")
    return "Analyst: " + ", ".join(bits) if bits else "The Analyst has flagged this one."


def _primary_contact(ctx: dict) -> dict | None:
    contacts = ctx.get("contacts") or []
    return contacts[0] if contacts else None


def _contact_label(ctx: dict) -> str:
    c = _primary_contact(ctx)
    if not c:
        return "the customer"
    return _person(c.get("name") or "") or c.get("email") or "the customer"


def _call_pending(opp: dict, ctx: dict) -> bool:
    """Somebody owes the customer a call: either the Analyst raised one and it is still
    Planned, or capture finished and nobody has spoken to them at all. Needs a contact — a
    'call' action with nobody to call is a card the rep cannot act on."""
    if not ctx.get("contacts"):
        return False
    calls = ctx.get("calls") or []
    if any((c.get("status") or "Planned") == "Planned" for c in calls):
        return True
    return bool(opp.get("captured_at")) and not calls


STEPS: tuple[Step, ...] = (
    Step(
        kind="analyze", lead_days=21, min_runway=5,
        pending=lambda o, c: not o.get("bid_decision") and not o.get("ingesting"),
        title=lambda o, c: f"Analyse “{_short(o)}”",
        reason=lambda o, c: "Nobody has read this yet — it needs a bid/no-bid read before "
                            "anything else can happen.",
    ),
    Step(
        kind="decide", lead_days=18, min_runway=4,
        pending=lambda o, c: o.get("bid_decision") == "Watch"
                             and (o.get("priority_score") or 0) >= DECIDE_PRIORITY_FLOOR,
        title=lambda o, c: f"Decide bid / no-bid — “{_short(o)}”",
        reason=lambda o, c: f"{_verdict_line(o)} — the Analyst stopped short of a call and "
                            f"wants a human on it.",
    ),
    Step(
        kind="approve_capture", lead_days=14, min_runway=3,
        pending=lambda o, c: o.get("bid_decision") == "Bid" and not o.get("capture_approved"),
        title=lambda o, c: f"Approve capture on “{_short(o)}”",
        reason=lambda o, c: f"{_verdict_line(o)} — capture is waiting on your go-ahead.",
    ),
    Step(
        kind="retry_capture", lead_days=12, min_runway=3,
        pending=lambda o, c: bool(o.get("capture_failed_at")),
        title=lambda o, c: f"Retry capture on “{_short(o)}”",
        reason=lambda o, c: f"The capture run failed: {(o.get('capture_error') or '')[:120] or 'no detail recorded'}",
    ),
    Step(
        kind="call", lead_days=10, min_runway=0,
        pending=_call_pending,
        title=lambda o, c: f"Call {_contact_label(c)}{_at_agency(o)}",
        reason=lambda o, c: (
            f"About “{_short(o, 70)}”. "
            + ("Capture is done — the customer call is the next move."
               if o.get("captured_at") else
               ((c.get("calls") or [{}])[0].get("talking_point") or
                "The Analyst raised this call."))
        ),
    ),
    Step(
        kind="review_docs", lead_days=7, min_runway=0,
        pending=lambda o, c: bool(o.get("captured_at")) and c.get("documents", 0) > 0
                             and not o.get("capture_reviewed_at"),
        title=lambda o, c: f"Review the capture documents for “{_short(o)}”",
        reason=lambda o, c: f"Capture produced {c.get('documents', 0)} document(s) that "
                            f"nobody has looked at.",
    ),
    Step(
        kind="submit", lead_days=3, min_runway=0,
        pending=lambda o, c: o.get("bid_decision") == "Bid" and bool(o.get("captured_at"))
                             and o.get("stage") != "Submitted",
        title=lambda o, c: f"Submit the response — “{_short(o)}”",
        reason=lambda o, c: "Everything upstream is done. Get it in.",
    ),
)

STEPS_BY_KIND = {s.kind: s for s in STEPS}
# The chain, as a set. `reply_mail` is deliberately not in it: it hangs off an opportunity but
# is not a stage of one, so the planner's "the chain moved on, close the rest" sweep must not
# reach it. A customer's question does not become answered because capture got approved.
CHAIN_KINDS: tuple[str, ...] = tuple(s.kind for s in STEPS)


def next_step(opp: dict, ctx: dict, blocked: set[str]) -> Optional[Step]:
    """The one thing this pursuit owes right now. `blocked` are steps a human already closed
    by hand — skipped, so dismissing a call moves the pursuit on instead of bringing the same
    card back tomorrow."""
    for step in STEPS:
        if step.kind in blocked:
            continue
        if step.pending(opp, ctx):
            return step
    return None


# ---------------------------------------------------------------------------------------
# The scheduler
# ---------------------------------------------------------------------------------------


def schedule(step: Step, deadline: Optional[date], today: date) -> tuple[str, str, bool]:
    """(due_on, urgency, infeasible) — the backward scheduling rule, and the whole point.

        days_left <= lead  ->  we are inside the window; it is due TODAY
        days_left >  lead  ->  it can wait; it surfaces on (deadline - lead)

    with no deadline meaning "no clock on it, so do the cheap step now". Urgency is how much
    slack is left against that runway, so an untouched task gets louder every morning without
    anybody bookkeeping it.
    """
    if deadline is None:
        return today.isoformat(), "normal", False

    days_left = (deadline - today).days
    due = today if days_left <= step.lead_days else deadline - timedelta(days=step.lead_days)

    # Urgency is the FRACTION of the runway left, not days of slack. Days of slack sounds
    # right and isn't: `decide` needs 18 days of runway and most solicitations close inside
    # 30, so every single deferral came out "critical" — 30 of them in one day on real data.
    # A word that describes everything describes nothing. As a fraction, a bid with a third
    # of its runway gone reads the same whether the step needs three weeks or three days.
    ratio = days_left / step.lead_days if step.lead_days else 0
    if ratio <= 0.34:
        urgency = "critical"
    elif ratio <= 0.67:
        urgency = "high"
    else:
        urgency = "normal"

    infeasible = step.min_runway > 0 and days_left < step.min_runway
    return due.isoformat(), urgency, infeasible


def _infeasible_reason(step: Step, deadline: Optional[date], today: date) -> str:
    days = (deadline - today).days if deadline else 0
    when = "closes today" if days <= 0 else f"closes in {days} day{'s' if days != 1 else ''}"
    return (f"This {when} and it is still only at “{step.kind.replace('_', ' ')}”. "
            f"Realistically it cannot be delivered — drop it, or escalate if it matters.")


# ---------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------


# Steps that remain worth doing after the solicitation has closed.
#
# The rest of the ladder is pre-submission: once the response date is gone you cannot
# analyse, decide, approve capture or submit your way back into a closed competition, so
# those steps expire with the deadline and stop asking. These two do not expire, because
# the deadline passing does not undo them:
#
#   call        — the customer relationship outlives one solicitation. A contracting
#                 officer you were about to speak to is still worth speaking to, and the
#                 next requirement from that office is the actual return on the call.
#   review_docs — capture already ran and produced deliverables. Nobody has read them.
#                 Expiring that is throwing away work that was already paid for.
POST_CAPTURE_KINDS: frozenset[str] = frozenset({"call", "review_docs"})


def _is_terminal(opp: dict, today: date) -> bool:
    """Whether the pursuit is over for ALL purposes.

    A human decision (No-Bid) or a terminal stage (Won / Lost / Submitted) ends everything.
    A passed deadline deliberately does NOT: it ends the pre-submission chain only, which
    `_deadline_passed` handles per-step. See POST_CAPTURE_KINDS.
    """
    return opp.get("bid_decision") in TERMINAL_DECISIONS or opp.get("stage") in TERMINAL_STAGES


def _deadline_passed(opp: dict, today: date) -> bool:
    deadline = _parse_day(opp.get("response_deadline"))
    return deadline is not None and deadline < today


def _users_by_email(organization_id: str) -> dict[str, str]:
    """email -> user id, for the org. Triage cards are keyed by mailbox address but actions
    are assigned by user id (mirroring an opportunity's own `assigned_to`)."""
    db = get_mongodb_client().get_database()
    cursor = db["users"].find(
        {"organizations.organization_id": organization_id}, {"_id": 1, "email": 1},
    )
    return {(u.get("email") or "").lower(): str(u["_id"]) for u in cursor if u.get("email")}


def plan_for_org(organization_id: str) -> dict:
    """Recompute one org's action plan. Idempotent — safe to call as often as you like.

    Reads are batched into a handful of queries and the close-outs into one write per outcome,
    because this runs over EVERY pursuit in the org: the per-opportunity version of the same
    logic was three reads and a write each, which on a few thousand pursuits is ten thousand
    round trips for a job that should be a few dozen.
    """
    crm = get_crm_store()
    today = _utc_today()
    horizon = today + timedelta(days=HORIZON_DAYS)

    crm.unsnooze_due_actions(organization_id)

    opps = crm.list_for_planning(organization_id)
    ids = [o["id"] for o in opps]
    calls_by_opp = crm.calls_by_opportunity(ids)
    doc_counts = crm.document_counts(ids)
    closed_by_opp = crm.closed_action_kinds(organization_id)

    terminal: list[str] = []
    no_step: list[str] = []
    by_step: dict[str, list[str]] = {}
    plans: list[dict] = []

    for opp in opps:
        opp_id = opp["id"]

        if _is_terminal(opp, today):
            terminal.append(opp_id)
            continue

        ctx = {
            "calls": calls_by_opp.get(opp_id, []),
            "documents": doc_counts.get(opp_id, 0),
            "contacts": crm.call_contacts(opp),
        }
        step = next_step(opp, ctx, closed_by_opp.get(opp_id, set()))
        if step is None:
            no_step.append(opp_id)
            continue
        # Past its response date, a pursuit keeps only its post-capture work. Counted as
        # terminal so the sweep below closes anything still open on the dead chain.
        if step.kind not in POST_CAPTURE_KINDS and _deadline_passed(opp, today):
            terminal.append(opp_id)
            continue
        by_step.setdefault(step.kind, []).append(opp_id)

        deadline = _parse_day(opp.get("response_deadline"))
        due_on, urgency, infeasible = schedule(step, deadline, today)
        if date.fromisoformat(due_on) > horizon:
            continue  # too far out to be worth a row; it gets written on its day

        # An impossible pursuit is only worth a person's attention if we had COMMITTED to it.
        # Walking away from a live Bid is a real decision and belongs on the list; a notice the
        # Analyst deferred and which then quietly ran out of runway is not news, and 49 cards
        # reading "this cannot be delivered, drop it" is not a day's work plan.
        if infeasible and opp.get("bid_decision") != "Bid":
            no_step.append(opp_id)
            by_step[step.kind].remove(opp_id)
            continue

        plans.append({
            "dedupe_key": dedupe_key(organization_id, step.kind, opp_id),
            "organization_id": organization_id,
            "assigned_to": opp.get("assigned_to") or [],
            "kind": step.kind,
            "opportunity_id": opp_id,
            "contact_email": (_primary_contact(ctx) or {}).get("email")
                             if step.kind == "call" else None,
            "ref_id": None,
            "title": step.title(opp, ctx),
            "reason": _infeasible_reason(step, deadline, today) if infeasible
                      else step.reason(opp, ctx),
            "due_on": due_on,
            "hard_deadline": deadline.isoformat() if deadline else None,
            "urgency": urgency,
            "infeasible": infeasible,
        })

    # The chain has moved on (or run out): anything else still open on these pursuits is
    # stale bookkeeping, and auto-closing it is what stops the rep ticking off work the
    # system can already see is done. Scoped to CHAIN_KINDS so an off-chain `reply_mail`
    # sitting on the same pursuit is never swept up with it.
    expired = crm.close_actions(organization_id, terminal, status="expired")
    crm.close_actions(organization_id, no_step, status="done", kinds=list(CHAIN_KINDS))
    for kind, kind_ids in by_step.items():
        crm.close_actions(
            organization_id, kind_ids, status="done",
            kinds=[k for k in CHAIN_KINDS if k != kind],
        )

    crm.upsert_actions(plans)

    replies = _plan_mail_replies(organization_id, today)
    logger.info(
        "action plan: org %s — %d actions written (%d replies), %d expired",
        organization_id, len(plans) + replies, replies, expired,
    )
    return {"organization_id": organization_id, "written": len(plans) + replies,
            "expired": expired}


def _plan_mail_replies(organization_id: str, today: date) -> int:
    """Off-chain: a customer wrote and is waiting.

    Always due today — a question from the customer does not wait for a runway to open. It is
    `high` when it is attached to a pursuit closing inside a fortnight, because that is when
    a slow reply actually costs something.
    """
    crm = get_crm_store()
    by_email = _users_by_email(organization_id)
    plans: list[dict] = []
    for card in crm.list_open_mail_triage(organization_id):
        opp = crm.get_opportunity(card.get("opportunity_id") or "", organization_id) or {}
        deadline = _parse_day(opp.get("response_deadline"))
        days_left = (deadline - today).days if deadline else None
        owner = by_email.get((card.get("employee_email") or "").lower())
        sender = card.get("sender_name") or card.get("sender_email") or "a customer"

        plans.append({
            "dedupe_key": dedupe_key(organization_id, "reply_mail", None, card["id"]),
            "organization_id": organization_id,
            "assigned_to": [owner] if owner else [],
            "kind": "reply_mail",
            "opportunity_id": card.get("opportunity_id"),
            "contact_email": card.get("sender_email"),
            "ref_id": card["id"],
            "title": f"Reply to {sender}",
            "reason": f"“{(card.get('subject') or 'no subject').strip()[:80]}” — "
                      + (f"about “{_short(opp, 60)}”, which closes in {days_left} days."
                         if days_left is not None and days_left >= 0
                         else "they are waiting on us."),
            "due_on": today.isoformat(),
            "hard_deadline": deadline.isoformat() if deadline else None,
            "urgency": "high" if days_left is not None and 0 <= days_left <= 14 else "normal",
            "infeasible": False,
        })
    crm.upsert_actions(plans)
    return len(plans)


@celery_app.task(name="action_plan.for_org")
def plan_org_actions(organization_id: str) -> dict:
    """On-demand single-org replan — fired after ingestion, analysis and capture, so the list
    reflects what just happened instead of waiting for tomorrow's beat."""
    return plan_for_org(organization_id)


# How long a pending replan absorbs further requests for the same org.
REPLAN_DEBOUNCE_SECONDS = 120


def request_replan(organization_id: str) -> bool:
    """Ask for a replan, at most once per org per couple of minutes. Returns whether one was
    actually scheduled.

    WHY DEBOUNCED AND NOT A CHORD. The obvious wiring is a chord over the Analyst batch, so
    the replan runs when the last verdict lands. But a chord body only fires if EVERY header
    task succeeded, and analyst tasks legitimately fail (a model timing out, a dead API key) —
    so the one thing guaranteed to skip the replan would be the day the batch went badly. This
    fires from the individual tasks instead, and the Redis key collapses three hundred of them
    into one sweep. It runs whether the batch succeeded, half-failed, or died.

    Best-effort throughout: a replan is a convenience over the daily beat, so a Redis blip must
    never take down the ingestion or capture path that called it.
    """
    try:
        import redis

        from app.settings import settings

        client = redis.Redis.from_url(f"{settings.redis_base_url}/0", decode_responses=True)
        # SET NX EX: the first caller wins the slot, everyone else in the window is a no-op.
        if not client.set(f"replan:{organization_id}", "1", nx=True, ex=REPLAN_DEBOUNCE_SECONDS):
            return False
        plan_org_actions.apply_async(
            args=[organization_id], countdown=REPLAN_DEBOUNCE_SECONDS,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not schedule a replan for org %s: %s", organization_id, exc)
        return False


@celery_app.task(name="action_plan.daily")
def daily_plan() -> dict:
    """Beat entrypoint — replan every org.

    One org failing must not cost the others their day's plan, so each is wrapped.
    """
    db = get_mongodb_client().get_database()
    results = []
    for org in db["organizations"].find({}, {"_id": 1}):
        oid = str(org["_id"])
        try:
            results.append(plan_for_org(oid))
        except Exception as exc:  # noqa: BLE001
            logger.error("action plan failed for org %s: %s", oid, exc, exc_info=True)
    return {"orgs": len(results), "written": sum(r["written"] for r in results)}
