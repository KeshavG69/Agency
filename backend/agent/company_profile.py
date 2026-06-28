"""Build a company's BD "fit lens" profile from its UEI → SAM.gov (Redis-cached).

Replaces the old hardcoded single-company profile: each org's agents are grounded in
THAT org's real registration data (legal name, NAICS, CAGE, location, status). The SAM.gov
entity is cached in Redis forever and busted when the admin saves a new UEI. Falls
back to the org name (and the last-saved snapshot) when SAM.gov data isn't available.
"""
from __future__ import annotations

import logging

from bson import ObjectId

from auth.database import get_mongodb_client
from utils.sam_gov import entity_cached

logger = logging.getLogger(__name__)


def _org(organization_id: str) -> dict | None:
    if not organization_id:
        return None
    db = get_mongodb_client().get_database()
    try:
        return db["organizations"].find_one({"_id": ObjectId(organization_id)})
    except Exception:  # noqa: BLE001 — non-ObjectId id
        return db["organizations"].find_one({"_id": organization_id})


def _format(name: str | None, uei: str | None, d: dict | None) -> str:
    if not d:
        if name:
            return (
                f"Company: {name}. (No SAM.gov profile on file — set the company's UEI in "
                "Organisation settings to ground judgments in its real registration data.)"
            )
        return "Company: (no company profile configured yet)."
    addr = d.get("physical_address") or {}
    loc = ", ".join(x for x in [addr.get("city"), addr.get("state"), addr.get("zip")] if x)
    naics = ", ".join(d.get("naics") or []) or "—"
    btypes = ", ".join(d.get("business_types") or []) or "—"
    expires = f", expires {d['registration_expiration']}" if d.get("registration_expiration") else ""
    return (
        f"Company: {d.get('legal_business_name') or name} "
        f"(UEI {d.get('uei') or uei or '—'}, CAGE {d.get('cage_code') or '—'})"
        f"{', ' + loc if loc else ''}.\n"
        f"SAM.gov registration: {d.get('registration_status') or 'unknown'}{expires}.\n"
        f"Socioeconomic / set-aside eligibility: {btypes}.\n"
        f"NAICS codes: {naics}.\n"
        "Use the NAICS codes + set-aside eligibility + registration as the capability / "
        "fit lens; do not assume capabilities or certifications beyond these and any "
        "provided documents."
    )


def company_context(organization_id: str) -> tuple[str, str]:
    """Return (company_name, profile_block) for an org.

    company_name is the legal/display name woven into agent prompts; profile_block is
    the multi-line "fit lens" the agent judges against. Driven by the org's UEI →
    SAM.gov (Redis-cached), with the org name + last-saved snapshot as fallbacks.
    """
    org = _org(organization_id) or {}
    name = org.get("name")
    uei = org.get("uei")
    details = None
    if uei:
        try:
            details = entity_cached(uei)
        except Exception as exc:  # noqa: BLE001 — SAM.gov key missing / fetch failed
            logger.warning("company_context: SAM.gov fetch failed for %s: %s", uei, exc)
    if not details:
        details = org.get("company_details")  # last snapshot saved by the UEI lookup
    display_name = (details or {}).get("legal_business_name") or name or "the company"
    return display_name, _format(name, uei, details)
