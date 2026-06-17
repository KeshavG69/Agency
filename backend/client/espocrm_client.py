"""EspoCRM integration client.

Thread-safe singleton (lazy init + cached), mirroring the other client/
integrations. Reads/writes EspoCRM — the single source of truth — via its REST
API (X-Api-Key auth). Upserts opportunities by solicitation number so repeated
syncs update rather than duplicate.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import httpx

from app.settings import settings
from models.opportunity import Opportunity
from models.verdict import AnalystVerdict


def _utc_now() -> str:
    """EspoCRM datetime format (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# EspoCRM enum options (values outside these are dropped to avoid validation errors)
_SET_ASIDE = {"WOSB", "8(a)", "SDVOSB", "HUBZone", "Small Business", "Full & Open"}
_OPP_TYPE = {
    "Solicitation", "Sources Sought", "Presolicitation",
    "Combined Synopsis/Solicitation", "Award Notice", "Special Notice",
}


def _coerce(value, allowed):
    return value if value in allowed else None


class EspoCRMClient:
    def __init__(self):
        self.base_url = settings.ESPOCRM_BASE_URL.rstrip("/")
        self.api_key = settings.ESPOCRM_API_KEY

    @property
    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=f"{self.base_url}/api/v1", headers=self._headers, timeout=30.0
        )

    def _to_payload(self, o: Opportunity) -> dict:
        payload: dict = {"name": o.title, "stage": o.stage or "Discover", "source": o.source}
        if o.solicitation_number:
            payload["solicitationNumber"] = o.solicitation_number
        if o.notice_id:
            payload["noticeId"] = o.notice_id
        if o.agency:
            payload["agency"] = o.agency
        if o.naics:
            payload["naics"] = o.naics
        if o.psc_code:
            payload["pscCode"] = o.psc_code
        if o.place_of_performance:
            payload["placeOfPerformance"] = o.place_of_performance
        if (sa := _coerce(o.set_aside, _SET_ASIDE)):
            payload["setAside"] = sa
        if (ot := _coerce(o.opp_type, _OPP_TYPE)):
            payload["oppType"] = ot
        if o.posted_date:
            payload["postedDate"] = o.posted_date
        if o.response_deadline:
            payload["responseDeadline"] = o.response_deadline
        if o.estimated_value is not None:
            payload["amount"] = o.estimated_value
            payload["amountCurrency"] = "USD"
        if o.poc_name:
            payload["pocName"] = o.poc_name
        if o.poc_email:
            payload["pocEmail"] = o.poc_email
        if o.link:
            payload["samLink"] = o.link
        if o.description:
            payload["description"] = o.description
        return payload

    def find_by_solicitation(self, sol: str) -> str | None:
        with self._client() as c:
            r = c.get("/Opportunity", params={
                "where[0][type]": "equals",
                "where[0][attribute]": "solicitationNumber",
                "where[0][value]": sol,
                "maxSize": 1,
            })
            r.raise_for_status()
            data = r.json()
            return data["list"][0]["id"] if data.get("total") else None

    def upsert_opportunity(self, o: Opportunity) -> tuple[str, str]:
        """Create or update an opportunity. Returns (action, crm_id)."""
        payload = self._to_payload(o)
        with self._client() as c:
            existing = self.find_by_solicitation(o.solicitation_number) if o.solicitation_number else None
            if existing:
                r = c.put(f"/Opportunity/{existing}", json=payload)
                r.raise_for_status()
                return "updated", existing
            r = c.post("/Opportunity", json=payload)
            r.raise_for_status()
            return "created", r.json()["id"]

    def count(self) -> int:
        with self._client() as c:
            r = c.get("/Opportunity", params={"maxSize": 1})
            r.raise_for_status()
            return r.json()["total"]

    # --- Analyst Agent support ---------------------------------------------

    def list_unanalyzed_opportunities(self, page_size: int = 200) -> list[dict]:
        """Return every opportunity the Analyst hasn't scored yet (analyzedAt is null).

        Paginates through the CRM so it scales past EspoCRM's per-request cap.
        """
        items: list[dict] = []
        offset = 0
        with self._client() as c:
            while True:
                r = c.get("/Opportunity", params={
                    "where[0][type]": "isNull",
                    "where[0][attribute]": "analyzedAt",
                    "maxSize": page_size,
                    "offset": offset,
                    "orderBy": "createdAt",
                    "order": "asc",
                })
                r.raise_for_status()
                data = r.json()
                batch = data.get("list", [])
                items.extend(batch)
                offset += len(batch)
                if not batch or offset >= data.get("total", 0):
                    break
        return items

    def apply_verdict(self, opportunity_id: str, verdict: AnalystVerdict) -> None:
        """Write the Analyst's verdict back and mark the opportunity analyzed."""
        payload = {
            "bidDecision": verdict.bid_decision,
            "priorityScore": verdict.priority_score,
            "analystRationale": verdict.rationale,
            "stage": verdict.recommended_stage,
            "analyzedAt": _utc_now(),
        }
        with self._client() as c:
            r = c.put(f"/Opportunity/{opportunity_id}", json=payload)
            r.raise_for_status()

    def create_call(
        self, opportunity_id: str, name: str, talking_point: str, direction: str = "Outbound"
    ) -> str:
        """Create a planned Call (a call-plan entry) linked to the opportunity."""
        start = datetime.now(timezone.utc)
        fmt = "%Y-%m-%d %H:%M:%S"
        payload = {
            "name": name,
            "status": "Planned",
            "direction": direction,
            "parentType": "Opportunity",
            "parentId": opportunity_id,
            "description": talking_point,
            "dateStart": start.strftime(fmt),
            "dateEnd": (start + timedelta(minutes=15)).strftime(fmt),
        }
        with self._client() as c:
            r = c.post("/Call", json=payload)
            r.raise_for_status()
            return r.json()["id"]

    def create_task(
        self, opportunity_id: str, name: str, description: str = "",
        due_date: str | None = None, priority: str = "Normal",
    ) -> str:
        """Create a follow-up Task linked to the opportunity."""
        payload = {
            "name": name,
            "status": "Not Started",
            "priority": priority,
            "parentType": "Opportunity",
            "parentId": opportunity_id,
            "description": description,
        }
        if due_date:
            payload["dateEnd"] = due_date
        with self._client() as c:
            r = c.post("/Task", json=payload)
            r.raise_for_status()
            return r.json()["id"]


_client: EspoCRMClient | None = None
_lock = threading.RLock()


def get_espocrm_client() -> EspoCRMClient:
    global _client
    with _lock:
        if _client is None:
            _client = EspoCRMClient()
        return _client
