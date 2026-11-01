"""Per-organization workflow settings: contract vehicles, the SAM.gov feed, and the task flow.

Not every customer bids the same way. A DoD capture shop pursues month-long solicitations it
finds on SAM.gov and calls the customer before it writes; a GSA schedule holder answers eBuy
RFQs (never on SAM.gov, so uploaded by hand) inside a week or two, with no call. These three
settings let one product serve both. All are admin-edited in Organisation settings, stored
under the org document's `settings`, and DEFAULT TO TODAY'S BEHAVIOUR, so an org that never
touches them sees no change.
"""
from __future__ import annotations

from agent.company_profile import _org

TASK_FLOWS = ("capture", "gsa_quote")

# Suggestions shown in the settings field — the org's own list is free text, because vehicles
# come and go and a firm may hold an agency-specific BPA nobody else has heard of.
SUGGESTED_VEHICLES = (
    "GSA MAS", "OASIS+", "Alliant 2", "8(a) STARS III", "VETS 2", "Polaris",
    "CIO-SP4", "NASA SEWP V", "ITES-3S", "GSA BPA",
)


def org_settings(organization_id: str) -> dict:
    """The three settings for one org, with defaults filled in."""
    s = ((_org(organization_id) or {}).get("settings") or {})
    feed = s.get("sam_feed") or {}
    flow = s.get("task_flow")
    return {
        "contract_vehicles": list(s.get("contract_vehicles") or []),
        "sam_feed": {
            "enabled": feed.get("enabled", True),
            "agencies": list(feed.get("agencies") or []),
        },
        "task_flow": flow if flow in TASK_FLOWS else "capture",
    }


def agency_allowed(agency: str | None, agencies: list[str]) -> bool:
    """Does a notice's agency path pass the org's agency filter? An empty filter passes all.

    Matched as a case-insensitive substring of the full path ("GENERAL SERVICES ADMINISTRATION
    › FEDERAL ACQUISITION SERVICE › …"), so "General Services Administration" keeps every GSA
    office and a narrower entry ("Federal Acquisition Service") keeps just that one.
    """
    if not agencies:
        return True
    path = (agency or "").lower()
    return any(a.strip().lower() in path for a in agencies if a.strip())
