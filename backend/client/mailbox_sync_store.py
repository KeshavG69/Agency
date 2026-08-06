"""mailbox_sync — the bookmark that turns the daily re-scan into an incremental sweep.

Until now the mail sweep re-read the most recent 400 messages on every run, stateless. That
caps how far back a busy mailbox is seen, and re-does the same work daily. trycompai/crm
instead keeps a per-mailbox cursor and reads only what is NEW since last time (see
docs/trycompai-deep-dive/03-backend-plumbing.md §3.2). This is our version of that cursor.

Composio exposes no Outlook `delta` tool, so the cursor is a TIMESTAMP high-water mark — the
`receivedDateTime` of the newest message we have processed — not an opaque delta token. The
sweep pages newest-first and stops the moment it reaches the mark.

ONE ROW PER MAILBOX, keyed (owner_email, organization_id). The mark only ever moves forward:
a message is counted exactly once, by whichever sweep first sees it. That "forward-only"
guarantee is what makes the accumulate-mode correspondence count in graph_store safe — see
docs/mail-sync-bookmark-and-ai-plan.md §A3.
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


class MailboxSyncStore:
    def __init__(self) -> None:
        client = MongoClient(settings.MONGODB_URL, tz_aware=True)
        self.db = client[settings.MONGODB_DATABASE]
        self.rows = self.db["mailbox_sync"]
        # Indexes live in utils/db_indexes.py: one row per mailbox — the lookup and the
        # uniqueness constraint are the same (owner_email, organization_id) key.

    @staticmethod
    def _key(owner_email: str, organization_id: str) -> dict:
        return {
            "owner_email": (owner_email or "").strip().lower(),
            "organization_id": (organization_id or "").strip(),
        }

    def get(self, owner_email: str, organization_id: str) -> Optional[dict]:
        return self.rows.find_one(self._key(owner_email, organization_id))

    def mark_running(self, owner_email: str, organization_id: str) -> None:
        """Best-effort 'in progress' flag. Creates the row on first sight."""
        key = self._key(owner_email, organization_id)
        now = _utc_now()
        self.rows.update_one(
            key,
            {
                "$set": {"status": "running", "updated_at": now},
                "$setOnInsert": {**key, "high_water": None, "backfilled": False,
                                 "first_synced_at": now, "last_error": None},
            },
            upsert=True,
        )

    def commit(
        self,
        owner_email: str,
        organization_id: str,
        high_water: Optional[str],
        *,
        backfilled: bool = True,
    ) -> None:
        """Record a successful sweep. `high_water` is the newest processed
        `receivedDateTime` (ISO string); it only ever moves FORWARD, so a sweep that
        happened to see nothing newer than the mark leaves it untouched."""
        key = self._key(owner_email, organization_id)
        now = _utc_now()
        prior = self.rows.find_one(key, {"high_water": 1})
        old = (prior or {}).get("high_water")
        # Lexicographic max is correct for ISO-8601 UTC ('...Z') timestamps, which is what
        # Graph returns for receivedDateTime. Guard against a None on either side.
        newest = max([v for v in (old, high_water) if v], default=None)
        self.rows.update_one(
            key,
            {
                "$set": {
                    "status": "idle",
                    "high_water": newest,
                    "backfilled": bool(backfilled),
                    "last_swept_at": now,
                    "updated_at": now,
                    "last_error": None,
                },
                "$setOnInsert": {**key, "first_synced_at": now},
            },
            upsert=True,
        )

    def mark_failed(self, owner_email: str, organization_id: str, error: str) -> None:
        key = self._key(owner_email, organization_id)
        now = _utc_now()
        self.rows.update_one(
            key,
            {
                "$set": {"status": "failed", "last_error": (error or "")[:500],
                         "updated_at": now},
                "$setOnInsert": {**key, "high_water": None, "backfilled": False,
                                 "first_synced_at": now},
            },
            upsert=True,
        )


_store: Optional[MailboxSyncStore] = None
_lock = threading.RLock()


def get_mailbox_sync_store() -> MailboxSyncStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = MailboxSyncStore()
    return _store
