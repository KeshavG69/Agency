"""SAM.gov ingestion — daily "pull once" via the bulk CSV.

SAM.gov has no webhooks, so "real-time" is a daily poll. Instead of fanning out a
keyed API call per NAICS (31 calls for Nexagen → burns the 1000/day budget + 429s),
we PULL ONCE: download SAM.gov's daily bulk CSV (~217 MB, no api_key, no quota),
parse it a single time, and filter locally by each org's NAICS. One download +
one parse covers every org. (This mirrors PriceIQ's RFP Radar.)

    download bulk CSV (cached/day) -> parse once, bucket by NAICS -> per org:
        filter to the org's NAICS -> upsert (deduped, org-tagged) -> Analyst batch

`daily_scan` (the beat entrypoint) parses ONCE for all orgs. `scan_org_sam` is the
on-demand single-org path (reuses the day's cached CSV, so no re-download).
"""
import logging

from agent.company_profile import _org
from app.worker import celery_app
from auth.database import get_mongodb_client
from client.crm_store import get_crm_store
from utils.sam_gov import bulk_opportunities_by_naics, entity_cached

logger = logging.getLogger(__name__)


def _org_naics(organization_id: str) -> list[str]:
    """The org's NAICS codes — from its UEI → SAM.gov entity (cached), or the last saved snapshot."""
    org = _org(organization_id) or {}
    details = None
    uei = org.get("uei")
    if uei:
        try:
            details = entity_cached(uei)
        except Exception as exc:  # noqa: BLE001 — SAM key missing / fetch failed
            logger.warning("SAM Radar: entity lookup failed for org %s: %s", organization_id, exc)
    if not details:
        details = org.get("company_details") or {}
    return [str(c) for c in (details.get("naics") or []) if c]


def _collect(buckets: dict, naics_codes: list[str]) -> list:
    """Flatten the org's NAICS buckets into a deduped (by notice_id) opportunity list."""
    seen: dict[str, object] = {}
    for code in naics_codes:
        for opp in buckets.get(code, []):
            if opp.notice_id and opp.notice_id not in seen:
                seen[opp.notice_id] = opp
    return list(seen.values())


def _ingest(organization_id: str, opps: list, analyze: bool = True) -> dict:
    """Upsert opps for one org; kick the Analyst batch only when `analyze` is set."""
    crm = get_crm_store()
    # The ~25s bulk-CSV parse can leave the pooled Mongo connection idle; wake it so the
    # first insert doesn't hit a stale/reset socket on the remote DB.
    try:
        crm.opps.database.client.admin.command("ping")
    except Exception:  # noqa: BLE001 — best-effort; the upsert retry below covers a miss
        pass
    created = updated = 0
    for o in opps:
        action = None
        for attempt in range(3):  # remote DB occasionally resets a connection mid-batch
            try:
                action, _ = crm.upsert_opportunity(o, organization_id)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    logger.warning("SAM Radar: upsert failed for %s: %s", o.notice_id, exc)
        created += action == "created"
        updated += action == "updated"
    # `analyze` is OFF for the on-demand "Pull from SAM.gov" flow: the user reviews the
    # matched opportunities and hand-picks which to send to the Analyst. The scheduled
    # daily scan keeps it ON (hands-off morning verdicts).
    if analyze and (created or updated):
        from tasks.analyst_tasks import run_analyst_batch  # lazy: avoid task import cycle
        run_analyst_batch.delay(organization_id)
    logger.info(
        "SAM Radar org %s: matched=%d created=%d updated=%d analyze=%s",
        organization_id, len(opps), created, updated, analyze,
    )
    return {"organization_id": organization_id, "found": len(opps),
            "created": created, "updated": updated}


@celery_app.task(bind=True, name="sam_radar.scan_org", max_retries=2, default_retry_delay=120)
def scan_org_sam(self, organization_id: str, lookback_days: int = 1, analyze: bool = False) -> dict:
    """On-demand single-org pull (reuses the day's cached bulk CSV — no re-download).

    `analyze` defaults to False: the pull only INGESTS the matched opportunities so the
    user can review + pick which to analyze. (The daily scan passes analyze=True.)
    """
    naics = _org_naics(organization_id)
    if not naics:
        logger.info("SAM Radar: org %s has no NAICS — skipping", organization_id)
        return {"organization_id": organization_id, "skipped": "no NAICS"}
    try:
        buckets = bulk_opportunities_by_naics(set(naics), posted_within_days=lookback_days)
    except Exception as exc:  # transient download/parse errors -> retry
        logger.warning("SAM Radar: bulk pull failed for org %s: %s", organization_id, exc)
        raise self.retry(exc=exc)
    return _ingest(organization_id, _collect(buckets, naics), analyze=analyze)


@celery_app.task(name="sam_radar.daily_scan")
def daily_scan(lookback_days: int = 1) -> dict:
    """Beat entrypoint — download + parse the bulk CSV ONCE, ingest for every org with NAICS."""
    db = get_mongodb_client().get_database()
    orgs = []
    union: set[str] = set()
    for org in db["organizations"].find({}, {"_id": 1}):
        oid = str(org["_id"])
        naics = _org_naics(oid)
        if naics:
            orgs.append((oid, naics))
            union.update(naics)
    if not orgs:
        return {"orgs": 0}

    buckets = bulk_opportunities_by_naics(union, posted_within_days=lookback_days)  # one parse
    results = [_ingest(oid, _collect(buckets, naics)) for oid, naics in orgs]
    total_new = sum(r["created"] for r in results)
    logger.info("SAM Radar daily_scan: %d orgs, %d new opportunities", len(orgs), total_new)
    return {"orgs": len(orgs), "new": total_new, "results": results}
