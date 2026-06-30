"""SAM.gov Entity Management API — fetch a company's registration by UEI.

Given an org's UEI (SAM.gov Unique Entity ID), pull the public entity record:
legal name, CAGE code, registration status, address, and NAICS codes — so the
Organisation settings can auto-fill company details. Needs a free api.data.gov /
SAM.gov API key (settings.SAM_GOV_API_KEY).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import date, timedelta

import httpx
import redis

from app.settings import settings
from models.opportunity import Opportunity

logger = logging.getLogger(__name__)

_redis: redis.Redis | None = None
_CACHE_KEY = "samgov:entity:{uei}"


def _redis_client() -> redis.Redis:
    global _redis
    if _redis is None:
        # from_url honours auth in REDIS_URL (Railway etc.); falls back to HOST/PORT locally.
        _redis = redis.Redis.from_url(f"{settings.redis_base_url}/0", decode_responses=True)
    return _redis


def entity_cached(uei: str) -> dict | None:
    """SAM.gov entity for a UEI, cached in Redis FOREVER (no TTL).

    Re-fetches from SAM.gov only on a cache miss or after `invalidate_entity()`.
    This is what the agents read (so a profile lookup is a single Redis GET).
    """
    uei = (uei or "").strip().upper()
    if not uei:
        return None
    key = _CACHE_KEY.format(uei=uei)
    try:
        hit = _redis_client().get(key)
        if hit is not None:
            return json.loads(hit)
    except Exception as exc:  # noqa: BLE001 — redis down: fall through to a live fetch
        logger.warning("redis get failed (%s); fetching SAM.gov live", exc)
    details = fetch_entity(uei)  # raises RuntimeError if no API key
    try:
        _redis_client().set(key, json.dumps(details))  # no expiry = forever
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis set failed: %s", exc)
    return details


def invalidate_entity(uei: str) -> None:
    """Drop a UEI's cached SAM.gov entity so the next fetch is fresh (called on Save)."""
    uei = (uei or "").strip().upper()
    if not uei:
        return
    try:
        _redis_client().delete(_CACHE_KEY.format(uei=uei))
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis delete failed: %s", exc)


def fetch_entity(uei: str) -> dict | None:
    """Look up one entity by UEI. Returns normalized company details, or None if not
    found. Raises RuntimeError if no API key is configured or the request fails."""
    uei = (uei or "").strip().upper()
    if not uei:
        return None
    if not settings.SAM_GOV_API_KEY:
        raise RuntimeError(
            "SAM_GOV_API_KEY is not configured — add a free SAM.gov / api.data.gov key "
            "to fetch entity details."
        )

    url = f"{settings.SAM_GOV_BASE_URL}/entity-information/v3/entities"
    params = {
        "ueiSAM": uei,
        "api_key": settings.SAM_GOV_API_KEY,
        "includeSections": "entityRegistration,coreData,assertions",
    }
    try:
        resp = httpx.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SAM.gov lookup failed for %s: %s", uei, exc)
        raise RuntimeError(f"SAM.gov lookup failed: {exc}")

    records = data.get("entityData") or []
    if not records:
        return None
    e = records[0]
    reg = e.get("entityRegistration") or {}
    core = e.get("coreData") or {}
    addr = core.get("physicalAddress") or {}
    naics_list = ((e.get("assertions") or {}).get("goodsAndServices") or {}).get("naicsList") or []
    naics = [n.get("naicsCode") for n in naics_list if n.get("naicsCode")]
    # Socioeconomic / business types (WOSB, EDWOSB, 8(a), SDVOSB, HUBZone, …) — the
    # set-aside eligibility the Analyst judges fit against.
    biz = core.get("businessTypes") or {}
    business_types = [
        t.get("businessTypeDesc") for t in (biz.get("businessTypeList") or []) if t.get("businessTypeDesc")
    ] + [
        t.get("sbaBusinessTypeDesc") for t in (biz.get("sbaBusinessTypeList") or []) if t.get("sbaBusinessTypeDesc")
    ]

    return {
        "uei": reg.get("ueiSAM") or uei,
        "legal_business_name": reg.get("legalBusinessName"),
        "cage_code": reg.get("cageCode"),
        "registration_status": reg.get("registrationStatus"),
        "registration_expiration": reg.get("registrationExpirationDate"),
        "physical_address": {
            "line1": addr.get("addressLine1"),
            "city": addr.get("city"),
            "state": addr.get("stateOrProvinceCode"),
            "zip": addr.get("zipCode"),
            "country": addr.get("countryCode"),
        },
        "entity_url": (core.get("entityInformation") or {}).get("entityURL"),
        "naics": naics,
        "business_types": list(dict.fromkeys(business_types)),  # de-duped, ordered
    }


# ---------------------------------------------------------------------------
# Opportunity search — SAM.gov Get Opportunities Public API v2.
# The org's NAICS codes are the filter: for each, pull recently-posted notices.
# SAM.gov has NO webhooks, so "real-time" = a daily poll (it refreshes ~once a
# day, ~03:30 GMT).
# ---------------------------------------------------------------------------
_OPP_SEARCH_URL = "/opportunities/v2/search"


def _clean_date(s: str | None) -> str | None:
    """SAM.gov dates come as ISO-ish strings; keep just YYYY-MM-DD."""
    return s[:10] if s else None


def _map_opportunity(raw: dict, fallback_naics: str | None = None) -> Opportunity:
    """Map one SAM.gov v2 `opportunitiesData` record onto our canonical Opportunity.

    NOTE: v2's `description` field is a URL (a separate fetch), not text — we leave
    `description` empty and carry the real attachment in `document_url` (parsed
    downstream to ground the Analyst).
    """
    pop = raw.get("placeOfPerformance") or {}
    pop_str = ", ".join(
        p for p in [
            (pop.get("city") or {}).get("name"),
            (pop.get("state") or {}).get("code"),
        ] if p
    ) or None

    poc_name = poc_email = None
    for poc in (raw.get("pointOfContact") or []):
        if poc.get("email"):
            poc_name, poc_email = poc.get("fullName"), poc.get("email")
            break

    resource_links = raw.get("resourceLinks") or []
    agency = (raw.get("fullParentPathName") or "").replace(".", " › ").strip() or None
    award = raw.get("award") or {}

    return Opportunity(
        title=raw.get("title") or "(untitled SAM.gov notice)",
        solicitation_number=raw.get("solicitationNumber"),
        notice_id=raw.get("noticeId"),
        agency=agency,
        naics=raw.get("naicsCode") or fallback_naics,
        psc_code=raw.get("classificationCode"),
        place_of_performance=pop_str,
        set_aside=raw.get("typeOfSetAsideDescription") or raw.get("typeOfSetAside"),
        opp_type=raw.get("type"),
        posted_date=_clean_date(raw.get("postedDate")),
        response_deadline=_clean_date(raw.get("responseDeadLine")),
        estimated_value=(float(award["amount"]) if str(award.get("amount") or "").strip()
                         .replace(".", "", 1).isdigit() else None),
        poc_name=poc_name,
        poc_email=poc_email,
        link=raw.get("uiLink"),
        document_url=resource_links[0] if resource_links else None,
        source="sam.gov",
        extra={
            "description_url": raw.get("description"),
            "resource_links": resource_links,
            "base_type": raw.get("baseType"),
            "active": raw.get("active"),
        },
    )


def search_opportunities(
    naics_codes: list[str],
    posted_from: str | None = None,
    posted_to: str | None = None,
    max_per_naics: int = 100,
    lookback_days: int = 1,
) -> list[Opportunity]:
    """Pull recently-posted SAM.gov notices for a set of NAICS codes (deduped by notice_id).

    `ncode` is single-valued, so we fan out one request per NAICS code. Dates default to
    a `lookback_days` window ending today (mm/dd/yyyy, as the API expects). One bad NAICS
    or a rate-limit (429) is logged and skipped — it never sinks the whole scan.
    """
    if not settings.SAM_GOV_API_KEY:
        raise RuntimeError(
            "SAM_GOV_API_KEY is not configured — add a SAM.gov / api.data.gov key to "
            "pull opportunities."
        )
    today = date.today()
    pf = posted_from or (today - timedelta(days=lookback_days)).strftime("%m/%d/%Y")
    pt = posted_to or today.strftime("%m/%d/%Y")
    url = f"{settings.SAM_GOV_BASE_URL}{_OPP_SEARCH_URL}"

    seen: dict[str, Opportunity] = {}
    for code in dict.fromkeys(c for c in naics_codes if c):  # de-dupe, keep order
        params = {
            "api_key": settings.SAM_GOV_API_KEY,
            "postedFrom": pf,
            "postedTo": pt,
            "ncode": code,
            "limit": min(max_per_naics, 1000),
            "offset": 0,
        }
        try:
            resp = httpx.get(url, params=params, timeout=30.0)
            if resp.status_code == 429:
                logger.warning("SAM.gov rate limit hit (429) at NAICS %s — stopping scan", code)
                break
            resp.raise_for_status()
            records = resp.json().get("opportunitiesData") or []
        except Exception as exc:  # noqa: BLE001 — one NAICS failing must not sink the scan
            logger.warning("SAM.gov opportunity search failed for NAICS %s: %s", code, exc)
            continue
        for raw in records:
            nid = raw.get("noticeId")
            if nid and nid not in seen:
                seen[nid] = _map_opportunity(raw, fallback_naics=code)
    logger.info(
        "SAM.gov: %d unique opportunities across %d NAICS (%s–%s)",
        len(seen), len(set(naics_codes)), pf, pt,
    )
    return list(seen.values())


# ---------------------------------------------------------------------------
# Bulk daily CSV — "pull once". SAM.gov publishes the full Contract Opportunities
# feed as one CSV (~217 MB) refreshed daily, with NO api_key and NO quota. One
# download covers every NAICS and every org, so we use it for the scan instead of
# fanning out a keyed API call per NAICS (which burns the 1000/day budget + 429s).
# Cached per-day on local disk so the daily scan + any on-demand pulls reuse it.
# ---------------------------------------------------------------------------
SAMGOV_BULK_CSV_URL = (
    "https://falextracts.s3.amazonaws.com/Contract%20Opportunities/datagov/"
    "ContractOpportunitiesFullCSV.csv"
)
_BULK_DOWNLOAD_TIMEOUT = 600.0  # 10 min — large download
_BULK_MIN_BYTES = 1_000_000     # a valid file is way bigger; guards against truncated cache


def _csv_row_to_opportunity(row: dict) -> Opportunity:
    """Map one bulk-CSV row → our canonical Opportunity. Unlike the v2 API, the CSV
    carries real `Description` TEXT (not a URL), which grounds the Analyst directly."""
    agency = " › ".join(
        p for p in [
            (row.get("Department/Ind.Agency") or "").strip(),
            (row.get("Sub-Tier") or "").strip(),
            (row.get("Office") or "").strip(),
        ] if p
    ) or None
    pop = ", ".join(
        p for p in [(row.get("PopCity") or "").strip(), (row.get("PopState") or "").strip()] if p
    ) or None
    award_amt = (row.get("Award$") or "").strip().replace("$", "").replace(",", "")

    return Opportunity(
        title=(row.get("Title") or "").strip() or "(untitled SAM.gov notice)",
        solicitation_number=(row.get("Sol#") or "").strip() or None,
        notice_id=(row.get("NoticeId") or "").strip() or None,
        agency=agency,
        naics=(row.get("NaicsCode") or "").strip() or None,
        psc_code=(row.get("ClassificationCode") or "").strip() or None,
        place_of_performance=pop,
        set_aside=(row.get("SetASide") or "").strip() or None,
        opp_type=(row.get("Type") or "").strip() or None,
        posted_date=_clean_date(row.get("PostedDate")),
        response_deadline=_clean_date(row.get("ResponseDeadLine")),
        estimated_value=float(award_amt) if award_amt.replace(".", "", 1).isdigit() else None,
        poc_name=(row.get("PrimaryContactFullname") or "").strip() or None,
        poc_email=(row.get("PrimaryContactEmail") or "").strip() or None,
        description=(row.get("Description") or "").strip() or None,
        link=(row.get("Link") or "").strip() or None,
        source="sam.gov",
        extra={
            "set_aside_code": (row.get("SetASideCode") or "").strip() or None,
            "additional_info_link": (row.get("AdditionalInfoLink") or "").strip() or None,
        },
    )


def download_bulk_csv(force: bool = False) -> str:
    """Download SAM.gov's daily bulk CSV, cached per-day on disk. Returns the path.

    The first caller of the day downloads (~217 MB); later callers (e.g. an on-demand
    pull after the scheduled scan) reuse the same file — so we download at most once/day.
    """
    cache = os.path.join(tempfile.gettempdir(), f"samgov_bulk_{date.today().isoformat()}.csv")
    if not force and os.path.exists(cache) and os.path.getsize(cache) >= _BULK_MIN_BYTES:
        logger.info("SAM.gov bulk CSV: reusing today's cached file %s", cache)
        return cache

    # Explicit per-operation timeouts: a stalled read (S3 hiccup) aborts the attempt fast
    # (60s) rather than hanging; the whole download still has a generous ceiling via retries.
    timeout = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)
    tmp = cache + ".part"
    last_exc: Exception | None = None
    for attempt in range(1, 4):  # the big S3 stream occasionally stalls — retry fresh
        total = 0
        try:
            with httpx.stream(
                "GET", SAMGOV_BULK_CSV_URL, timeout=timeout, follow_redirects=True,
                headers={"Accept": "*/*"},
            ) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1 << 16):
                        f.write(chunk)
                        total += len(chunk)
            os.replace(tmp, cache)  # atomic: only a complete download becomes the cache
            logger.info("SAM.gov bulk CSV: downloaded %.1f MB → %s", total / 1024 / 1024, cache)
            _purge_old_bulk_csv(keep=os.path.basename(cache))
            return cache
        except Exception as exc:  # noqa: BLE001 — retry the whole stream on any read/conn error
            last_exc = exc
            logger.warning("SAM.gov bulk CSV: download attempt %d/3 failed (%s)", attempt, exc)
            try:
                os.path.exists(tmp) and os.unlink(tmp)
            except OSError:
                pass
    raise RuntimeError(f"SAM.gov bulk CSV download failed after 3 attempts: {last_exc}")


def _purge_old_bulk_csv(keep: str) -> None:
    """Delete prior days' cached bulk CSVs (each ~217 MB) so temp doesn't fill up."""
    tmpdir = tempfile.gettempdir()
    try:
        for name in os.listdir(tmpdir):
            if name.startswith("samgov_bulk_") and name != keep:
                try:
                    os.unlink(os.path.join(tmpdir, name))
                except OSError:
                    pass
    except OSError:
        pass


def bulk_opportunities_by_naics(
    wanted_naics: set[str], posted_within_days: int = 1, active_only: bool = True,
    open_only: bool = True,
) -> dict[str, list[Opportunity]]:
    """Stream-parse today's bulk CSV ONCE → {naics: [Opportunity, ...]} for the wanted NAICS.

    Filters to the given NAICS set + recently-posted as it streams, so memory stays bounded
    even though the file is huge. One parse serves every org. Rows are dropped when:
      - NAICS not wanted
      - `active_only` and the notice is not Active
      - posted before the `posted_within_days` cutoff
      - `open_only` and the ResponseDeadLine has already passed (still-biddable only) —
        notices with no deadline (e.g. some Sources Sought) are KEPT.
    """
    if not wanted_naics:
        return {}
    path = download_bulk_csv()
    today_iso = date.today().isoformat()
    cutoff = date.today() - timedelta(days=posted_within_days) if posted_within_days > 0 else None
    buckets: dict[str, list[Opportunity]] = defaultdict(list)
    scanned = kept = expired = 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            scanned += 1
            naics = (row.get("NaicsCode") or "").strip()
            if naics not in wanted_naics:
                continue
            if active_only and (row.get("Active") or "").strip().lower() != "yes":
                continue
            if cutoff is not None:
                posted = _clean_date(row.get("PostedDate"))
                if not posted or posted < cutoff.isoformat():
                    continue
            if open_only:
                deadline = _clean_date(row.get("ResponseDeadLine"))
                if deadline and deadline < today_iso:  # response window already closed
                    expired += 1
                    continue
            buckets[naics].append(_csv_row_to_opportunity(row))
            kept += 1
    logger.info(
        "SAM.gov bulk CSV: %d rows scanned, %d kept (%d dropped as past-deadline) across %d NAICS (last %dd)",
        scanned, kept, expired, len(buckets), posted_within_days,
    )
    return buckets
