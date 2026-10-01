"""Relationship cadence — who has a warm contact gone quiet with, and by how long.

This is the arithmetic half of the relationship engine. It answers, for one employee's
network, a question the opportunity flow never asks: *which warm relationships are overdue
for a touch, right now, independent of any deal?*

WHY THERE IS NO MODEL HERE. Same reason the action planner has none: given the same
`corr_count` and `last_contact` this must return the same answer every day, cost nothing per
contact across a network of thousands, and be inspectable — "warm (42 emails), 47 days quiet,
expected ~30" is a sentence a rep can check. An LLM would be slow, priced per contact, and
non-deterministic. The *judgement* (what to say) is the agent's job; deciding *who is overdue*
is subtraction.

EXPECTED CADENCE IS DERIVED FROM WARMTH, and the buckets deliberately mirror the tiers the
Mail Agent already uses (`agent/mail_agent.py::_relationship_context`): a frequent contact
(>=10) is expected on a tighter rhythm than a developing one (3-9); anyone lighter than that
is too thin to nudge on and is skipped, so the engine never manufactures noise from a
one-off exchange.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Sequence

# corr_count threshold -> expected days between touches. Ordered high-to-low; the first
# threshold a contact meets wins. Below the lowest threshold, cadence is None = "do not
# nudge" (too little history to say a silence means anything).
#
# TUNED FOR GOVCON, not consumer sales. Deal cycles run 6-18 months and contacts are sparse,
# so a monthly "just checking in" to a program manager reads as spam. Warm contacts get a
# roughly quarterly rhythm, developing ones semi-annual. These are deliberately conservative
# defaults; making them per-org configurable is the real fix (see docs/relationship-engine.md).
_CADENCE_BUCKETS: tuple[tuple[int, int], ...] = (
    (10, 90),   # warm / frequent  -> ~quarterly
    (3, 180),   # developing       -> ~semi-annual
)

# Past this many days silent a relationship is LAPSED, not "going cold": the contact has very
# likely moved on, retired, or changed jobs, and a "been a while!" note lands as a cold email
# to a stranger. We surface the freshly-lapsed, not the long-dead — so these are dropped from
# the nudge set entirely (an archive/re-qualify candidate, not a one-tap send).
LAPSED_CEILING_DAYS = 240


@dataclass(frozen=True)
class CadenceConfig:
    """One organisation's touch rhythm.

    WHY THIS IS A SETTING AND NOT A CONSTANT. "How often should we be in front of this person"
    is a judgement that differs by firm and by the data a firm actually has: a shop working
    long DoD recompetes touches quarterly, one chasing task orders far more often. Worse, the
    rhythm has to be reachable — an org whose mailbox history only goes back two months can
    never have anyone 90 days overdue, so a hard-coded 90 means the engine produces nothing
    and looks broken. Admins set this; the defaults below are the govcon-sane starting point.
    """

    warm_threshold: int = _CADENCE_BUCKETS[0][0]
    warm_days: int = _CADENCE_BUCKETS[0][1]
    developing_threshold: int = _CADENCE_BUCKETS[1][0]
    developing_days: int = _CADENCE_BUCKETS[1][1]
    lapsed_after_days: int = LAPSED_CEILING_DAYS

    @property
    def buckets(self) -> tuple[tuple[int, int], ...]:
        return (
            (self.warm_threshold, self.warm_days),
            (self.developing_threshold, self.developing_days),
        )


DEFAULT_CADENCE = CadenceConfig()

# The keys an admin may set under organizations.settings.relationship_cadence.
CADENCE_SETTING_KEYS = (
    "warm_threshold", "warm_days",
    "developing_threshold", "developing_days",
    "lapsed_after_days",
)


def cadence_from_settings(settings: Optional[dict]) -> CadenceConfig:
    """Build a config from an org's stored settings, ignoring anything malformed.

    Fail-soft on purpose: a bad value in a settings document must fall back to the default,
    never take the nightly sweep down for that whole organisation.
    """
    raw = (settings or {}).get("relationship_cadence") or {}
    if not isinstance(raw, dict):
        return DEFAULT_CADENCE
    values: dict = {}
    for key in CADENCE_SETTING_KEYS:
        if key in raw:
            try:
                v = int(raw[key])
            except (TypeError, ValueError):
                continue
            if v > 0:
                values[key] = v
    return CadenceConfig(**values) if values else DEFAULT_CADENCE


def get_org_cadence(organization_id: str) -> CadenceConfig:
    """This organisation's cadence, or the default when unset/unreadable."""
    try:
        from auth.database import get_mongodb_client
        from bson import ObjectId

        ids: list = [organization_id]
        try:
            ids.append(ObjectId(str(organization_id)))
        except Exception:  # noqa: BLE001
            pass
        db = get_mongodb_client().get_database()
        org = db["organizations"].find_one({"_id": {"$in": ids}}, {"settings": 1}) or {}
        return cadence_from_settings(org.get("settings"))
    except Exception:  # noqa: BLE001 — a settings read must never break the sweep
        return DEFAULT_CADENCE


def expected_cadence_days(
    corr_count: int, config: Optional[CadenceConfig] = None
) -> Optional[int]:
    """The touch rhythm a relationship of this warmth is expected to keep, or None when the
    contact is too light to nudge on at all."""
    n = int(corr_count or 0)
    for threshold, days in (config or DEFAULT_CADENCE).buckets:
        if n >= threshold:
            return days
    return None


def _as_date(value) -> Optional[date]:
    """Parse a stored `last_contact` (an ISO-8601 string, occasionally already a datetime)
    into a date. Returns None for anything unparseable — a contact we cannot date is a
    contact we do not nudge, rather than one we guess is ancient."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # Take the date portion; tolerate a trailing 'Z' the stdlib pre-3.11 chokes on.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _today(today: Optional[date]) -> date:
    return today or datetime.now(timezone.utc).date()


def staleness_days(
    corr_count: int, last_contact, today: Optional[date] = None,
    config: Optional[CadenceConfig] = None,
) -> Optional[int]:
    """How many days OVERDUE this relationship is for a touch. Positive = overdue by that
    many days; <= 0 = comfortable. None = not a candidate (too light, or undatable).

    overdue = days_since(last_contact) - expected_cadence(warmth)
    """
    expected = expected_cadence_days(corr_count, config)
    if expected is None:
        return None
    seen = _as_date(last_contact)
    if seen is None:
        return None
    days_since = (_today(today) - seen).days
    return days_since - expected


def select_going_cold(
    rows: Sequence[dict],
    today: Optional[date] = None,
    cap: Optional[int] = None,
    lapsed_after: Optional[int] = None,
    config: Optional[CadenceConfig] = None,
) -> list[dict]:
    """From an employee's relationship rows, the ones worth a touch today — the warmest,
    freshly-lapsed relationships first, capped so a day's nudges are a short list not a wall.

    THE ORDERING IS THE PRODUCT. Ranking by "most overdue" (the obvious sort) is actively
    wrong: on a real mailbox the top of that list is every contact silent for a year —
    retired officers, dead procurements, people who changed employer — so a rep dismisses
    junk for two days and stops opening the list. We instead drop anything past
    `lapsed_after` and rank by WARMTH first, then by who lapsed most recently: the 40-email
    relationship that just slipped its rhythm is the one a rep actually wants to rescue.

    Each input row needs `corr_count` and `last_contact`; every other key (email, name,
    company) passes through. Each returned row gains:
      expected_cadence  the rhythm this warmth implies (days)
      days_since        days since the last touch
      overdue_by        days past the expected cadence (always > 0 here)
    so the caller — and ultimately the rep's card — can show exactly why it fired.
    """
    cfg = config or DEFAULT_CADENCE
    ceiling = lapsed_after if lapsed_after is not None else cfg.lapsed_after_days
    ref = _today(today)
    out: list[dict] = []
    for row in rows:
        corr = int(row.get("corr_count") or 0)
        expected = expected_cadence_days(corr, cfg)
        if expected is None:
            continue
        seen = _as_date(row.get("last_contact"))
        if seen is None:
            continue
        days_since = (ref - seen).days
        overdue_by = days_since - expected
        if overdue_by <= 0:
            continue
        if days_since > ceiling:
            continue  # lapsed, not going cold — not a one-tap "been a while" note
        out.append(
            {
                **row,
                "expected_cadence": expected,
                "days_since": days_since,
                "overdue_by": overdue_by,
            }
        )
    # Warmest first; among equals, the one that lapsed most recently (smallest overdue).
    out.sort(key=lambda r: (-int(r.get("corr_count") or 0), r["overdue_by"]))
    return out[:cap] if cap else out
