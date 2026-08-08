"""agent_events — an append-only record of what each agent did, and why.

Every agent in Collecct already produces this text: the Analyst writes a `rationale`,
the Relation agent a `reason` per contact, the research agent an outcome line. All of it
is thrown away the moment the value it accompanies is stored. So when a rep asks "why is
this a No-Bid?" or "why was this person surfaced?", there is nothing to show them.

This keeps the trail. Nothing here changes what an agent decides — it records the
decision so the UI can display it later. Capture it now: you cannot backfill history you
never wrote down.

APPEND-ONLY. Events are never updated or deleted, which is what makes the log worth
trusting when a decision is being questioned.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient

from app.settings import settings

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EventsStore:
    def __init__(self) -> None:
        client = MongoClient(settings.MONGODB_URL, tz_aware=True)
        self.db = client[settings.MONGODB_DATABASE]
        self.events = self.db["agent_events"]
        # Indexes live in utils/db_indexes.py: (org, subject.id, created_at) serves the only
        # read that matters — the trail for one record, oldest first.

    def record(
        self,
        organization_id: str,
        agent: str,
        subject_type: str,
        subject_id: str,
        step: str,
        detail: str = "",
        *,
        ok: bool = True,
        tool: Optional[str] = None,
    ) -> Optional[str]:
        """Append one step. `step` is a short verb phrase ("judged the opportunity");
        `detail` is the sentence a rep reads. `ok=False` marks a rejection or a miss —
        the UI renders those differently, because "why NOT" is the question people
        actually ask."""
        org = (organization_id or "").strip()
        sid = (subject_id or "").strip()
        if not org or not sid or not step:
            return None
        try:
            return str(self.events.insert_one({
                "organization_id": org,
                "agent": agent,
                "subject": {"type": subject_type, "id": sid},
                "step": step,
                "detail": (detail or "")[:2000],
                "tool": tool,
                "ok": ok,
                "created_at": _utc_now(),
            }).inserted_id)
        except Exception as exc:  # noqa: BLE001 — an audit-trail miss must never break work
            logger.warning("agent_events: could not record %r: %s", step, exc)
            return None

    def trail(self, organization_id: str, subject_id: str, limit: int = 200) -> list[dict]:
        """Everything that happened to one record, oldest first."""
        rows = self.events.find(
            {"organization_id": (organization_id or "").strip(),
             "subject.id": (subject_id or "").strip()},
            {"_id": 0},
        ).sort("created_at", 1).limit(limit)
        return list(rows)


_store: Optional[EventsStore] = None
_lock = threading.RLock()


def get_events_store() -> EventsStore:
    global _store
    with _lock:
        if _store is None:
            _store = EventsStore()
        return _store


def record_event(organization_id: str, agent: str, subject_type: str, subject_id: str,
                 step: str, detail: str = "", *, ok: bool = True,
                 tool: Optional[str] = None) -> None:
    """Fire-and-forget convenience wrapper — never raises, so call sites stay clean."""
    try:
        get_events_store().record(organization_id, agent, subject_type, subject_id,
                                  step, detail, ok=ok, tool=tool)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_events: %s", exc)
