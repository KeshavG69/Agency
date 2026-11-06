"""One capability matrix per opportunity (collection `capability_matrices`)."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient, ReturnDocument

from app.settings import settings

BUILDING, READY, FAILED = "building", "ready", "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CapabilityStore:
    def __init__(self) -> None:
        client = MongoClient(settings.MONGODB_URL, tz_aware=True)
        self.rows = client[settings.MONGODB_DATABASE]["capability_matrices"]

    def _key(self, organization_id: str, opportunity_id: str) -> dict:
        return {"organization_id": organization_id, "opportunity_id": opportunity_id}

    def get(self, organization_id: str, opportunity_id: str) -> Optional[dict]:
        return self.rows.find_one(self._key(organization_id, opportunity_id), {"_id": 0})

    def _set(self, organization_id: str, opportunity_id: str, fields: dict) -> Optional[dict]:
        return self.rows.find_one_and_update(
            self._key(organization_id, opportunity_id),
            {"$set": {**fields, "updated_at": _now()},
             "$setOnInsert": {"created_at": _now()}},
            upsert=True, projection={"_id": 0}, return_document=ReturnDocument.AFTER,
        )

    def mark_building(self, organization_id: str, opportunity_id: str) -> Optional[dict]:
        # The last matrix stays visible while a new one is built.
        return self._set(organization_id, opportunity_id, {"status": BUILDING, "error": None})

    def mark_failed(self, organization_id: str, opportunity_id: str, error: str) -> None:
        self._set(organization_id, opportunity_id, {"status": FAILED, "error": error[:500]})

    def save(self, organization_id: str, opportunity_id: str, areas: list[dict], contacts: list[dict],
             stats: dict) -> Optional[dict]:
        return self._set(organization_id, opportunity_id, {
            "status": READY, "error": None, "areas": areas, "contacts": contacts, "stats": stats,
            "built_at": _now(),
        })

    def set_file(self, organization_id: str, opportunity_id: str, file: dict | None,
                 file_error: str | None = None) -> None:
        self._set(organization_id, opportunity_id, {"file": file, "file_error": file_error})


_store: CapabilityStore | None = None
_lock = threading.Lock()


def get_capability_store() -> CapabilityStore:
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = CapabilityStore()
    return _store
