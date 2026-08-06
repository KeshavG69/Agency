"""Free company enrichment from an email domain — replaces the paid Explorium person
enrichment for the contact graph.

Instead of buying per-person data (title/LinkedIn), we resolve the one signal that actually
drives govcon teaming recommendations — the CONTACT'S COMPANY and what it does — for free:

  email domain  ->  People Data Labs FREE Company Dataset (7M+ companies, self-hosted in the
                    local `companies` Mongo collection)  ->  {name, industry, size, linkedin}

Not in the dataset? We still derive a display company name from the domain and flag it
`company_needs_research: True` so the CRM agent (which already has web search) can research it
when the contact is actually relevant to a bid. No paid API, no per-lookup cost, no caps.

Load the dataset once with scripts/load_pdl_companies.py; until then the derive-name fallback
still runs, so this is always an improvement over paying Explorium for everything.
"""
from __future__ import annotations

import logging
import re

from pymongo import MongoClient

from app.settings import settings

logger = logging.getLogger(__name__)

# Free mailbox / provider domains — NOT companies. A contact on one of these has no company.
_PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "aol.com", "icloud.com", "me.com", "mac.com", "protonmail.com",
    "proton.me", "pm.me", "gmx.com", "zoho.com", "yandex.com", "mail.com", "fastmail.com",
}
# Common mail subdomains stripped so mail.acme.com ~ acme.com (mirrors graph_store._norm_domain).
_MAIL_SUB = re.compile(r"^(?:mail|smtp|email|mx\d*|mailer|mailgun|e|em|send|sendgrid|go|info|newsletter|marketing|notifications?)\.")

_client: MongoClient | None = None


def _companies():
    """The local `companies` collection (PDL Free Company Dataset), keyed by domain."""
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGODB_URL)
    return _client[settings.MONGODB_DATABASE]["companies"]


def norm_domain(d: str | None) -> str:
    """Lowercase, strip a common mail subdomain, drop a trailing dot/path."""
    d = (d or "").strip().lower()
    if not d:
        return ""
    d = d.split("/")[0].strip(".")
    stripped = _MAIL_SUB.sub("", d)
    # Only accept the stripped form if it still looks like a registrable domain (has a dot).
    return stripped if "." in stripped else d


def domain_of(contact: dict) -> str:
    """The contact's email domain (normalized)."""
    d = contact.get("domain")
    if not d:
        email = (contact.get("email") or "").strip().lower()
        d = email.split("@", 1)[1] if "@" in email else ""
    return norm_domain(d)


# Suffixes where the registrable name is the THIRD label from the right, not the second.
# A short list, not the full Public Suffix List: govcon contacts are overwhelmingly .com /
# .gov / .mil / .org, and pulling in a PSL dependency to serve a handful of edge cases is a
# poor trade. An unlisted multi-part suffix simply falls back to the stricter comparison.
_MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "com.au", "net.au", "org.au",
    "co.nz", "co.za", "com.br", "com.mx", "co.in", "com.sg",
})


def registrable_domain(host: str) -> str:
    """The company-owned part of a hostname: aai.textron.com -> textron.com.

    Approximate by design (see _MULTI_SUFFIXES). Returns the host unchanged when it is
    already short enough to be registrable.
    """
    labels = [x for x in (host or "").split(".") if x]
    if len(labels) < 3:
        return ".".join(labels)
    return ".".join(labels[-3:] if ".".join(labels[-2:]) in _MULTI_SUFFIXES else labels[-2:])


def company_name_from_domain(domain: str) -> str:
    """A human-ish company name from a domain — the fallback when the dataset has no match.
    e.g. 'raytheon.com' -> 'Raytheon', 'general-dynamics.com' -> 'General Dynamics'."""
    if not domain:
        return ""
    sld = domain.split(".")[0]
    return re.sub(r"[-_]+", " ", sld).strip().title()


# US government / military domains are NOT commercial companies. The PDL dataset either lacks
# them or maps a generic gov mailbox to one random org (mail.mil -> a single medical center),
# so for these we skip the dataset entirely and derive the AGENCY name from the domain.
_GOV_SUFFIXES = (".gov", ".mil")

# Common federal agencies, keyed by the label immediately left of the .gov/.mil TLD.
_AGENCY_NAMES = {
    "army": "US Army", "navy": "US Navy", "af": "US Air Force", "airforce": "US Air Force",
    "marines": "US Marine Corps", "usmc": "US Marine Corps", "uscg": "US Coast Guard",
    "spaceforce": "US Space Force", "mail": "U.S. Department of Defense",
    "dod": "U.S. Department of Defense", "defense": "U.S. Department of Defense",
    "dcma": "DCMA", "dla": "DLA", "disa": "DISA", "darpa": "DARPA", "dia": "DIA",
    "nga": "NGA", "nsa": "NSA", "socom": "USSOCOM", "centcom": "USCENTCOM",
    "nasa": "NASA", "gsa": "GSA", "dhs": "DHS", "hhs": "HHS", "epa": "EPA", "faa": "FAA",
    "fbi": "FBI", "irs": "IRS", "treasury": "U.S. Department of the Treasury",
    "state": "U.S. Department of State", "justice": "U.S. Department of Justice",
    "doj": "U.S. Department of Justice", "usda": "USDA", "noaa": "NOAA", "cdc": "CDC",
    "nih": "NIH", "fema": "FEMA", "uspto": "USPTO", "sba": "SBA", "sec": "SEC",
    "ssa": "SSA", "usaid": "USAID", "gao": "GAO", "opm": "OPM", "nist": "NIST",
    "nrc": "NRC", "tsa": "TSA", "cbp": "CBP", "va": "U.S. Department of Veterans Affairs",
    "energy": "U.S. Department of Energy", "doe": "U.S. Department of Energy",
    "commerce": "U.S. Department of Commerce", "dot": "U.S. Department of Transportation",
    "hud": "HUD", "dol": "U.S. Department of Labor", "ed": "U.S. Department of Education",
    "doi": "U.S. Department of the Interior", "census": "U.S. Census Bureau",
}


def is_gov_domain(domain: str) -> bool:
    """True for US government / military domains (…​.gov, …​.mil, and their subdomains)."""
    return bool(domain) and domain.endswith(_GOV_SUFFIXES)


def agency_name_from_domain(domain: str) -> str:
    """A clean agency label from a .gov/.mil domain — the AGENCY (the label nearest the TLD),
    not a commercial company. e.g. 'us.army.mil' -> 'US Army', 'dcma.mil' -> 'DCMA',
    'jpl.nasa.gov' -> 'NASA'."""
    head = domain
    for suf in _GOV_SUFFIXES:
        if domain.endswith(suf):
            head = domain[: -len(suf)]
            break
    labels = [x for x in head.split(".") if x]
    key = labels[-1] if labels else ""
    if not key:
        return "U.S. Government"
    if key in _AGENCY_NAMES:
        return _AGENCY_NAMES[key]
    # Unknown agency: short labels are almost always acronyms (uppercase), else title-case.
    return key.upper() if len(key) <= 4 else re.sub(r"[-_]+", " ", key).title()


def lookup_companies(domains: set[str]) -> dict[str, dict]:
    """Batch domain -> company record from the local PDL dataset. Empty dict if the
    dataset hasn't been loaded yet (the caller falls back to the derived name)."""
    wanted = {d for d in domains if d and d not in _PERSONAL_DOMAINS}
    if not wanted:
        return {}
    # FALL BACK TO THE REGISTRABLE DOMAIN. The dataset is keyed on the company's own domain,
    # so a regional or divisional subdomain misses: `ibm.com` is present, `us.ibm.com` is not,
    # and ten IBM employees were being marked unenriched and queued for paid research to
    # rediscover that IBM is IBM. Same company, so the parent's record is the right answer.
    reg = {d: registrable_domain(d) for d in wanted}
    try:
        cur = _companies().find(
            {"domain": {"$in": list(wanted | {r for r in reg.values() if r})}}
        )
        found = {r["domain"]: r for r in cur}
    except Exception as exc:  # noqa: BLE001 — no dataset / mongo hiccup -> derive-name fallback
        logger.warning("company dataset lookup failed (falling back to derived names): %s", exc)
        return {}
    # Exact match wins; the parent domain is only consulted when the exact one missed.
    return {d: rec for d in wanted if (rec := found.get(d) or found.get(reg[d]))}


def _evidence(kind: str, detail: str, existing: str = "") -> list[dict]:
    """The observation(s) behind a company name — see models/evidence.py.

    When Outlook's own contact card already carried a company, that is a SECOND,
    independent source for the same employer, so it is recorded as its own entry rather
    than silently replacing the domain observation.
    """
    out = [{"kind": kind, "detail": detail}]
    if existing:
        out.append(
            {
                "kind": "outlook.address-book",
                "detail": f'their Outlook contact card says "{existing}"',
            }
        )
    return out


def enrich_contacts_company(contacts: list[dict]) -> list[dict]:
    """Resolve each contact's COMPANY (+ industry / website / linkedin) for free from its email
    domain via the PDL Free Company Dataset. Personal-mailbox contacts get no company. Unknown
    company domains keep a derived name and `company_needs_research=True` for the agent to research.

    Returns the same list with company fields filled, `enriched: bool` (True only when the
    dataset had a match — so downstream 'enriched' counts stay meaningful), and `evidence`:
    WHERE the company name came from, so a dataset match (a lookup) and a name derived off
    the domain (a guess) stop being indistinguishable downstream. The evidence is scored in
    models/evidence.py and stored by client/facts_store.py; the graph write ignores the key."""
    # Government/military domains skip the commercial dataset entirely (see is_gov_domain).
    by_domain = lookup_companies({domain_of(c) for c in contacts if not is_gov_domain(domain_of(c))})
    out: list[dict] = []
    for c in contacts:
        dom = domain_of(c)
        existing = (c.get("company") or "").strip()
        if is_gov_domain(dom):  # government/military — derive the agency, not a commercial company
            out.append({
                **c,
                "company": existing or agency_name_from_domain(dom),
                "industry": c.get("industry") or "Government / Public Sector",
                "domain": dom,
                "enriched": False,
                "company_needs_research": False,
                "government": True,
                # A deterministic table, not a guess: us.army.mil IS the US Army.
                "evidence": _evidence(
                    "gov-domain-rule",
                    f"{dom} is a US government/military domain", existing,
                ),
            })
            continue
        rec = by_domain.get(dom)
        if rec:  # dataset hit — the good case
            out.append({
                **c,
                "company": existing or rec.get("name") or company_name_from_domain(dom),
                "industry": rec.get("industry"),
                "company_size": rec.get("size"),
                "company_website": rec.get("website") or rec.get("domain"),
                "company_linkedin": rec.get("linkedin"),
                "domain": dom,
                "enriched": True,
                "company_needs_research": False,
                # A lookup in a curated dataset — strong enough to assert on the record.
                "evidence": _evidence(
                    "pdl.domain-company",
                    f'{dom} matched "{rec.get("name") or dom}" in the company dataset',
                    existing,
                ),
            })
        elif dom and dom not in _PERSONAL_DOMAINS:  # a real company domain, just not in the dataset
            out.append({
                **c,
                "company": existing or company_name_from_domain(dom),
                "domain": dom,
                "enriched": False,
                "company_needs_research": True,  # researched by research_company_task
                # THE GUESS. raytheon.com -> "Raytheon" is usually right and occasionally
                # embarrassing, so it is offered as a suggestion and never asserted.
                "evidence": _evidence(
                    "domain-derived-name",
                    f"the company name was derived from the domain {dom}", existing,
                ),
            })
        else:  # personal mailbox / no domain — no company to attach
            out.append({**c, "domain": dom, "enriched": False,
                        "company_needs_research": False, "evidence": []})
    return out
