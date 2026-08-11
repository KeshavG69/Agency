"""The daily action plan — one row per thing a HUMAN must do, on a given day.

WHY THIS EXISTS. The Pipeline shows *objects*: "here are 40 opportunities, the Analyst
thinks these are Bids." The rep then has to derive, per row, what he is actually supposed
to do about it. That derivation is the work nobody wants. An action is that derivation,
already done: a verb, with a subject, due on a specific day.

WHY IT IS ITS OWN COLLECTION and not computed on read. An action has a life of its own —
done, snoozed to Thursday, dismissed. A derived view cannot remember that a human pushed
something back.

WHY IT IS NOT `agent_tasks`. That queue is for MACHINE work: leases, retries, budgets,
stand-downs, a worker claiming a row. This is human work — no lease, no retry, and it needs
snooze/dismiss instead. Same idea, incompatible lifecycle. They coexist.

THE SCHEDULER IS THE POINT. `due_on` is worked BACKWARDS from the opportunity's response
deadline (see tasks/action_plan_tasks.py). A bid closing in three days compresses its whole
remaining chain onto today; one closing in a month contributes only its first step, on the
day that step's runway starts. Same pursuit, same steps — the deadline decides which day
each lands on and how loud it is.

Spec: docs/daily-action-plan.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# The steps of a pursuit, in order. `reply_mail` is deliberately NOT one of them — it comes
# from the mail triage queue, not from a pursuit's lifecycle, and is always due today.
ActionKind = Literal[
    "analyze",          # nobody has read this yet
    "decide",           # the Analyst said Watch — a human owes a call
    "approve_capture",  # it is a Bid; capture needs the go-ahead
    "retry_capture",    # the capture run died
    "call",             # talk to the customer
    "review_docs",      # capture produced documents nobody has looked at
    "submit",           # get the response in
    "reply_mail",       # off-chain: a customer wrote and is waiting
]

# critical = we are past the point this step should have been done
# high     = two days or less of slack left
# normal   = comfortable
Urgency = Literal["critical", "high", "normal"]

# open      -> on somebody's list
# done      -> finished (by hand, or auto-closed when the underlying state satisfied it)
# snoozed   -> pushed to `snoozed_to`; the planner re-opens it that morning
# dismissed -> "not doing this", by choice
# expired   -> the pursuit went terminal (No-Bid / Won / Lost / deadline passed) with the
#              action still open. Distinct from `dismissed` so "he decided not to" and "it
#              stopped being relevant" never look the same in the history.
ActionStatus = Literal["open", "done", "snoozed", "dismissed", "expired"]


class Action(BaseModel):
    """One task on one day for one human."""

    organization_id: str
    # Mirrors the opportunity's own `assigned_to` (a LIST of user ids), so the same RBAC
    # rule applies unchanged: empty means unassigned, which everyone in the org can see.
    assigned_to: list[str] = Field(default_factory=list)

    kind: ActionKind
    opportunity_id: Optional[str] = None
    contact_email: Optional[str] = Field(None, description="call / reply_mail")
    ref_id: Optional[str] = Field(None, description="call_id, triage card id — what it acts on")

    title: str = Field(description="Imperative, already rendered: 'Call Donna Scandaliato at DLA'")
    reason: str = Field(description="Why this, why now — one line under the title")

    due_on: str = Field(description="YYYY-MM-DD — the day it lands on the list")
    hard_deadline: Optional[str] = Field(None, description="The opportunity's response deadline")
    urgency: Urgency = "normal"
    infeasible: bool = Field(
        False,
        description="Not enough runway left to finish the chain. Shown plainly rather than "
                    "hidden — the honest move is to say so and offer Dismiss, not to pretend "
                    "it is a normal task.",
    )

    status: ActionStatus = "open"
    snoozed_to: Optional[str] = None
    completed_at: Optional[datetime] = None
    auto_completed: bool = Field(
        False, description="Closed by the planner because the underlying state satisfied it"
    )

    # The idempotency contract. The planner runs daily AND on every pipeline event, so it
    # must never be able to create a second "Approve capture on GITSS-A". Every write is an
    # upsert on this key.
    dedupe_key: str

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def dedupe_key(
    organization_id: str, kind: str, opportunity_id: str | None, discriminator: str | None = None
) -> str:
    """The uniqueness key for an action.

    For the chain steps, (org, kind, opportunity) IS the identity — one pursuit has one next
    step. Notably the `call` action does NOT key on the contact: the card opens the Call Plan
    dialog, which already has a tab per person, so keying per contact would only mean the card
    duplicates itself the day the primary contact changes.

    `discriminator` is for the off-chain kinds that are genuinely one-per-thing — `reply_mail`
    keys on the triage card id, because two mails from the same customer are two replies.
    """
    return ":".join((
        organization_id,
        kind,
        opportunity_id or "",
        (discriminator or "").strip().lower(),
    ))
