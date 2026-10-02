"""Today's date, for agent prompts — and the instruction to actually reason with it.

WHY EVERY AGENT NEEDS THIS. A model has no clock. Given no date it falls back on whatever "now"
it can infer — its training cutoff, or the most recent date it happens to see in the context.
An end-to-end run on a live opportunity showed both failure modes in one pass:

  * the Call Brief called a solicitation topic that had closed two months earlier "the relevant
    active topic" and told the rep to ask the partner whether they were "actively shaping a
    response" to it;
  * the Capture agent dated its plan 7 July 2026 when it was run on 2 October 2026 — it had
    latched onto the record's `analyzed_at` timestamp — and then wrote a self-contradicting
    sentence calling an 4 August deadline "already elapsed as of 7 July".

Only the Analyst was given the date, and it was the only agent that got the deadlines right
(it lowered its priority because it knew the named topic had expired).

WHY UTC. The action planner's rule applies here too: never `date.today()` / `datetime.now()`,
which return the SERVER's local date and can be a day ahead of UTC. Everything in this system —
deadlines, `due_on`, `last_contact` — is a UTC calendar day, so "today" must be one as well.

WHY IT IS AN INSTRUCTION, NOT JUST A DATE. Stating the date is necessary but not sufficient: a
model told only "today is X" will still repeat a deadline from the source material as if it
were upcoming. The line says what to do with it.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Bookkeeping timestamps on an opportunity record. They describe when OUR SYSTEM touched the
# record, not anything about the solicitation, and they are the most recent-looking dates in the
# prompt — so an agent without a clock reads one as "today". Agents that render the whole
# record should drop these.
RECORD_BOOKKEEPING_FIELDS = frozenset({
    "analyzed_at", "created_at", "updated_at", "ingested_at", "captured_at",
    "contacts_searched_at", "outreach_drafted_at", "decided_at", "synced_at",
})


def utc_today() -> str:
    """Today as a UTC calendar day, YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def date_context(today: str | None = None) -> str:
    """The line every agent prompt opens with: the date, and what it means for deadlines."""
    day = today or utc_today()
    try:
        weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%A")
    except ValueError:
        weekday = ""
    label = f"{day} ({weekday})" if weekday else day
    return (
        f"TODAY'S DATE: {label}.\n"
        "Use this as the current date. Any deadline, due date, or response date BEFORE it has "
        "already PASSED — never describe a passed deadline as upcoming, open, or active, and "
        "date any document you write with this date, not with a date found in the material."
    )
