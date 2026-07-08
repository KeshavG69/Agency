"""Explorium enrichment — resolve a contact's name, title, and company from email.

Two-step flow (per Explorium's API):
  1. MATCH  POST /v1/prospects/match  { prospects_to_match: [{email}] } -> prospect_id
  2. FETCH  POST /v1/prospects        { filters: {prospect_id: {values:[...]}} }
            -> full_name, job_title, job_department_main, company_name

Used to enrich Outlook contacts before they become nodes in the knowledge graph.
Commercial coverage (teaming partners / primes / subs) is strong; government
(.mil/.gov) contacts usually won't resolve — that's expected.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.settings import settings

logger = logging.getLogger(__name__)

_MATCH_BATCH = 50   # Explorium match accepts batches
_FETCH_BATCH = 100  # fetch page_size


def _headers() -> dict:
    return {"api_key": settings.EXPLORIUM_API_KEY, "Content-Type": "application/json"}


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def match_by_email(emails: list[str]) -> dict[str, str]:
    """Map each email -> prospect_id (omits emails Explorium can't match).

    Enrichment is best-effort — a bad batch (quota, rate limit, transient outage)
    is logged and skipped rather than losing every contact in the ingest.
    """
    if not emails:
        return {}
    out: dict[str, str] = {}
    url = f"{settings.EXPLORIUM_BASE_URL}/prospects/match"
    with httpx.Client(timeout=60) as client:
        for batch in _chunks(emails, _MATCH_BATCH):
            body = {"request_context": {}, "prospects_to_match": [{"email": e} for e in batch]}
            try:
                r = client.post(url, headers=_headers(), json=body)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Explorium match_by_email batch failed (%d emails): %s", len(batch), exc)
                continue
            for m in r.json().get("matched_prospects", []):
                email = (m.get("input") or {}).get("email")
                pid = m.get("prospect_id")
                if email and pid:
                    out[email] = pid
    return out


# The Explorium "full" fields we keep on the Person node (scalars + string-arrays
# — all FalkorDB-storable). Everything useful for ranking/outreach/teaming.
_PROSPECT_FIELDS = (
    "first_name", "last_name", "full_name",
    "job_title", "job_department_main", "job_level_main",
    "company_name", "company_website", "company_linkedin",
    "linkedin", "country_name", "region_name", "city",
    "skills", "experience", "business_id",
)


def fetch_prospects(prospect_ids: list[str]) -> dict[str, dict]:
    """Map prospect_id -> the full profile (every field in _PROSPECT_FIELDS).

    Enrichment is best-effort — a bad batch (quota, rate limit, transient outage)
    is logged and skipped rather than losing every contact in the ingest.
    """
    if not prospect_ids:
        return {}
    out: dict[str, dict] = {}
    url = f"{settings.EXPLORIUM_BASE_URL}/prospects"
    with httpx.Client(timeout=60) as client:
        for batch in _chunks(prospect_ids, _FETCH_BATCH):
            body = {
                "mode": "full",
                "page_size": _FETCH_BATCH,
                "page": 1,
                "filters": {"prospect_id": {"values": batch}},
            }
            try:
                r = client.post(url, headers=_headers(), json=body)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Explorium fetch_prospects batch failed (%d ids): %s", len(batch), exc)
                continue
            payload = r.json()
            rows = payload.get("data") or payload.get("prospects") or payload.get("results") or []
            for row in rows:
                pid = row.get("prospect_id")
                if pid:
                    out[pid] = {k: row.get(k) for k in _PROSPECT_FIELDS}
    return out


def enrich_contacts(contacts: list[dict]) -> list[dict]:
    """Resolve name / title / company for contacts via Explorium (by email).

    Each input contact: { name, email, company, title }. Returns the same list
    with Explorium values filled in where found, plus `enriched: bool`.
    """
    if not settings.EXPLORIUM_API_KEY:
        logger.warning("EXPLORIUM_API_KEY not set — skipping enrichment.")
        return [{**c, "enriched": False} for c in contacts]

    emails = [c["email"] for c in contacts if c.get("email")]
    email_to_pid = match_by_email(emails)
    pid_to_profile = fetch_prospects(list(set(email_to_pid.values())))

    enriched: list[dict] = []
    for c in contacts:
        pid = email_to_pid.get(c.get("email"))
        prof = pid_to_profile.get(pid) if pid else None
        if prof:
            enriched.append({
                **c,
                # primary fields (kept for the existing graph schema / search)
                "name": prof.get("full_name") or c.get("name"),
                "title": prof.get("job_title") or c.get("title"),
                "company": prof.get("company_name") or c.get("company"),
                "department": prof.get("job_department_main"),
                "prospect_id": pid,
                "enriched": True,
                # full enrichment — everything else Explorium returned
                "first_name": prof.get("first_name"),
                "last_name": prof.get("last_name"),
                "seniority": prof.get("job_level_main"),
                "company_website": prof.get("company_website"),
                "company_linkedin": prof.get("company_linkedin"),
                "linkedin": prof.get("linkedin"),
                "country": prof.get("country_name"),
                "region": prof.get("region_name"),
                "city": prof.get("city"),
                "skills": prof.get("skills"),
                "experience": prof.get("experience"),
                "business_id": prof.get("business_id"),
            })
        else:
            enriched.append({**c, "enriched": False})
    return enriched
