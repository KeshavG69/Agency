"""The relationship engine's output — a drafted outreach a rep approves with one tap.

Two shapes here:

  * `RelationshipDraft` — what the agent returns: a subject and a short body, plus the facts
    it drew on. Deliberately leaner than MailDraft (no SharePoint grounding, no cc) — a
    "how've you been / up for golf?" note is not a capability pitch.

  * `RelationshipNudge` — what we persist and the Today card reads: the draft, the contact,
    the machine-checked evidence line explaining why it fired, and the proof-gate result. An
    Action of kind relationship_touch / relationship_personal points at one of these by id.

WHY THE EVIDENCE IS A STORED FIELD, not prose the agent wrote. The reason a nudge fired —
"warm (42 emails), quiet 47 days; you mentioned golf Aug 3" — is assembled from the cadence
arithmetic and the stored facts, NOT authored by the model. The product rule is that a
judgement shows its evidence; letting the model narrate its own reason would let it narrate
one that isn't true.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

NudgeKind = Literal["relationship_touch", "relationship_personal"]
NudgeStatus = Literal["open", "approved", "dismissed", "expired"]


class RelationshipDraft(BaseModel):
    """The agent's drafted message. Short, in the rep's voice, no hard sell."""

    subject: str = Field(..., description="A short, human subject line.")
    body: str = Field(..., description="The message body, plain text. Brief and personal.")
    drew_on: list[str] = Field(
        default_factory=list,
        description="The stored facts/signals the draft used, verbatim — e.g. "
        "'played golf 2026-08-03'. Checked against the store by the proof gate.",
    )


class RelationshipNudge(BaseModel):
    """One drafted outreach, waiting for a rep's one-tap approve."""

    organization_id: str
    owner_email: str = Field(description="The employee whose relationship this is.")
    kind: NudgeKind

    contact_email: str
    contact_name: Optional[str] = None
    contact_company: Optional[str] = None

    subject: str
    body: str
    # The machine-assembled 'why this, why now' — from cadence + facts, never the model.
    reason: str
    # The stored facts the draft leaned on (for the card's evidence panel).
    drew_on: list[str] = Field(default_factory=list)

    # Cadence signals, carried so the card can show them without another query.
    corr_count: int = 0
    days_since: Optional[int] = None
    overdue_by: Optional[int] = None

    # The last thing these two actually said to each other. This is the evidence a rep checks
    # before sending — "quiet 47 days" is a metric, this is a reason.
    last_subject: Optional[str] = None
    last_contact: Optional[str] = None  # YYYY-MM-DD

    status: NudgeStatus = "open"

    # One row per (owner, contact, kind) — re-running the sweep refreshes, never duplicates.
    dedupe_key: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
