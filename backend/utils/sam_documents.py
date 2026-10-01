"""Fetch a SAM.gov notice's solicitation documents and turn them into `document_text`.

WHY THIS EXISTS. The product's core claim is that every agent judgement is grounded in the
actual solicitation (the PWS), not the notice summary. An audit found that was true for 3 of
10,320 opportunities — the manual uploads. All 10,317 SAM.gov notices carried the summary only:
neither ingestion path (the bulk CSV, nor the public-search fallback it has run on since the CSV
went dark) ever looked at attachments, and the keyed v2 API that does return `resourceLinks`
was answering 401. So the Analyst was making bid/no-bid calls without ever reading the PWS.

THE SOURCE. sam.gov's own website lists a notice's attachments through a keyless endpoint:

    GET https://sam.gov/api/prod/opps/v3/opportunities/{noticeId}/resources
    GET https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{resourceId}/download
        -> 302 to a presigned S3 copy of the real file

Same family as the public-search fallback in utils/sam_gov.py: no API key, no quota, but
UNDOCUMENTED — GSA can change it without notice, so every call here is fail-soft and a notice
we cannot read simply keeps its summary.

WHICH FILES. A real package is a mix of the one document that matters and a lot that doesn't.
On a live notice: the PWS, the solicitation in two versions (an amendment and its successor),
a FAR clauses attachment, and two Q&A matrices. Parsing all of it spends the context budget on
boilerplate and duplicates, so selection is deliberate:

  1. only real, current, public files — never `link` entries, deleted (superseded) attachments,
     export-controlled files, or formats the parser cannot read;
  2. one copy per document — versions/amendments of the same file collapse to the newest;
  3. ranked by decision value — PWS/SOW/SOO first, then the solicitation and its evaluation
     criteria, then everything else; clause/provision/wage-determination/blank-form boilerplate
     is dropped outright;
  4. capped by count and size, so a 500-page package cannot run up parse and digest cost.

Every attachment's fate (kept, or why not) is returned, so the record can show what was read.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_RESOURCES_URL = "https://sam.gov/api/prod/opps/v3/opportunities/{nid}/resources"
DOWNLOAD_URL = "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{rid}/download"
# `Accept: application/hal+json` is required by this API family (anything else -> 406).
_HEADERS = {
    "User-Agent": "Collecct/1.0 (govcon BD pipeline; contact via SAM.gov registrant)",
    "Accept": "application/hal+json",
}
_TIMEOUT = httpx.Timeout(connect=15.0, read=45.0, write=15.0, pool=15.0)

# Formats LiteParse reads as documents. Images are deliberately excluded: in a solicitation
# they are diagrams and logos, and OCR on them costs time for no decision value. Archives
# (.zip) are excluded because their contents are unknown until unpacked.
PARSEABLE_EXT = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".rtf", ".csv",
    ".htm", ".html",
})

MAX_FILES = 8                     # documents parsed per notice
MAX_FILE_BYTES = 25 * 1024 ** 2   # one file larger than this is almost always a scanned binder
MAX_TOTAL_BYTES = 60 * 1024 ** 2  # whole package
MAX_CHARS_PER_FILE = 150_000      # one huge document must not crowd out the PWS in the digest

# Tier 0 — the document that defines the work. This is what the Analyst most needs.
_TIER0 = re.compile(
    r"\b(pws|sow|soo|performance\s+work\s+statement|statement\s+of\s+(work|objectives?)"
    r"|requirements?\s+(document|statement)|technical\s+(requirements?|specifications?)"
    r"|(program|performance|technical|mission)\s+objectives?|specifications?)\b",
    re.I,
)
# Tier 1 — the solicitation itself and how it will be evaluated.
_TIER1 = re.compile(
    r"\b(solicitation|rfp|rfq|rfi|request\s+for\s+(proposals?|quot\w*|information)"
    r"|combined\s+synopsis|sources?\s+sought|section\s+[lm]\b|evaluation"
    r"|instructions?\s+to\s+offerors?|notice\s+of\s+intent|presolicitation|baa"
    r"|broad\s+agency\s+announcement|white\s*papers?|rwp|request\s+for\s+solutions?"
    r"|commercial\s+solutions?\s+opening|cso|call\s+for\s+(proposals?|papers?|solutions?)"
    # Agency-specific names for the solicitation itself: the FAA issues a Screening
    # Information Request (SIR) instead of an RFP, and task-order competitions issue a TOR /
    # RFTOP / fair-opportunity notice. Missed, these ranked as mere "supporting" files.
    r"|sir|screening\s+information\s+request|rftop|tor|task\s+order\s+(request|proposal)"
    r"|fair\s+opportunity)\b",
    re.I,
)
# Boilerplate — long, standard, and nearly identical across every notice. Dropped outright:
# it carries no signal for a bid/no-bid call and would consume the context budget.
_BOILERPLATE = re.compile(
    r"\b(clauses?|provisions?|wage\s+determination|sca\s+wd|davis[-\s]bacon"
    r"|sf[-\s]?\d{2,4}|standard\s+form|past\s+performance\s+questionnaire|ppq"
    r"|pricing\s+(template|sheet|schedule)|price\s+schedule|representations?\s+and\s+certifications?"
    r"|reps?\s*(and|&)\s*certs?|templates?|questionnaires?|privacy\s+act"
    r"|key\s+person(nel)?\s+(form|expanded|profile)|guide\s+to\s+format"
    r"|format(ting)?\s+(guide|instructions?))\b",
    re.I,
)
# Tokens that mark a VERSION of a document rather than a different document. Stripped when
# grouping, so "…Solicitation -Amend 2.pdf" and "…Solicitation v4.pdf" are seen as one file.
_VERSION_TOKENS = re.compile(
    r"\b(amend(ment)?|amd|mod(ification)?|rev(ision)?|ver(sion)?|v)\s*[-_.]?\s*\w{0,4}\b"
    r"|\b(final|draft|updated?|revised|corrected|clean|redline|copy)\b"
    r"|\(\s*\d+\s*\)"
    r"|\b\d{1,2}[-_/.]\d{1,2}[-_/.]\d{2,4}\b|\b20\d{2}[-_.]?\d{2}[-_.]?\d{2}\b"
    r"|\b\d{1,2}[-\s]*(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-\s,]*(\d{2,4})?\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[-\s]*\d{1,2}(st|nd|rd|th)?,?[-\s]*\d{2,4}\b"
    r"|(?<=[a-z])v\d{1,3}\b"
    r"|\b(govt|government)\s+responses?\b|\bresponses?\s+to\s+questions\b",
    re.I,
)


@dataclass
class Attachment:
    name: str
    resource_id: str
    size: int
    posted: str
    ext: str
    tier: int = 2
    selected: bool = False
    reason: str = ""

    @property
    def download_url(self) -> str:
        return DOWNLOAD_URL.format(rid=self.resource_id)

    def manifest(self) -> dict:
        return {"name": self.name, "resource_id": self.resource_id, "size": self.size,
                "posted": self.posted, "selected": self.selected, "reason": self.reason}


@dataclass
class SolicitationDocuments:
    text: str = ""
    document_url: Optional[str] = None
    fingerprint: str = ""
    attachments: list[dict] = field(default_factory=list)
    parsed: int = 0
    error: Optional[str] = None


def _words(name: str) -> str:
    """Filename with separators as spaces, so word-boundary rules work on real SAM names.

    `_` is a regex WORD character, so `\bTemplate` never matched "Paper_Template" and
    `\bAmd` never matched "HQ086025S0001_Amd2" — and SAM.gov filenames use underscores
    constantly. Every classification below matches against this form, never the raw name.
    """
    return re.sub(r"[_]+", " ", name or "")


def _ext(name: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]{1,5})\s*$", name or "")
    return (m.group(1) if m else "").lower()


def _base(name: str) -> str:
    """A version-insensitive key: same document across amendments/revisions -> same key."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}\s*$", "", name or "")
    stem = _VERSION_TOKENS.sub(" ", _words(stem))
    stem = re.sub(r"[^a-z0-9]+", " ", stem.lower())
    return re.sub(r"\s+", " ", stem).strip()


def _tier(name: str) -> int:
    words = _words(name)
    if _TIER0.search(words):
        return 0
    if _TIER1.search(words):
        return 1
    return 2


def list_attachments(notice_id: str) -> Optional[list[dict]]:
    """The raw attachment records SAM.gov holds for a notice.

    Returns [] when the notice genuinely has no attachments and None when the request FAILED.
    The two must stay distinct: the caller records "nothing to fetch" for [] and stops asking,
    so collapsing a transient network error into [] would mark the notice as document-less
    permanently. None means "unknown — try again next time".
    """
    try:
        resp = httpx.get(_RESOURCES_URL.format(nid=notice_id), headers=_HEADERS,
                         timeout=_TIMEOUT, params={"excludeDeleted": "true"})
        resp.raise_for_status()
        groups = ((resp.json() or {}).get("_embedded") or {}).get("opportunityAttachmentList") or []
    except Exception as exc:  # noqa: BLE001 — an unreadable notice keeps its summary
        logger.info("SAM.gov attachments unavailable for %s: %s", notice_id, exc)
        return None
    return [a for g in groups for a in (g.get("attachments") or [])]


def fingerprint(raw: list[dict]) -> str:
    """Identity of the notice's CURRENT document set. Changes when an amendment adds or
    replaces a file — which is exactly when the text must be rebuilt."""
    ids = sorted(
        str(a.get("resourceId")) for a in raw
        if a.get("type") == "file" and str(a.get("deletedFlag") or "0") in ("0", "false", "False")
    )
    return hashlib.sha1("|".join(ids).encode()).hexdigest()[:16] if ids else ""


def select_attachments(raw: list[dict]) -> list[Attachment]:
    """Decide, for every attachment, whether it is parsed — and record why not."""
    out: list[Attachment] = []
    for a in raw:
        name = (a.get("name") or "").strip() or "(unnamed)"
        att = Attachment(
            name=name, resource_id=str(a.get("resourceId") or ""),
            size=int(a.get("size") or 0), posted=str(a.get("postedDate") or ""),
            ext=_ext(name) or (a.get("mimeType") or "").lower(),
        )
        if a.get("type") != "file":
            att.reason = "a link, not a file"
        elif str(a.get("deletedFlag") or "0") not in ("0", "false", "False"):
            att.reason = "deleted / superseded"
        elif (a.get("accessLevel") or "public") != "public" or str(a.get("explicitAccess") or "0") not in ("0", "false"):
            att.reason = "not public (requires SAM.gov access)"
        elif str(a.get("exportControlled") or "0") not in ("0", "false", "False"):
            att.reason = "export-controlled"
        elif not att.resource_id:
            att.reason = "no resource id"
        elif att.ext not in PARSEABLE_EXT:
            att.reason = f"format {att.ext or '?'} not parsed"
        elif att.size and att.size > MAX_FILE_BYTES:
            att.reason = f"too large ({att.size // 1024 ** 2} MB)"
        elif _BOILERPLATE.search(_words(name)):
            att.reason = "boilerplate (clauses / forms / pricing)"
        out.append(att)

    candidates = [a for a in out if not a.reason]

    # One copy per document: keep the newest version of each version-insensitive name.
    by_base: dict[str, Attachment] = {}
    for a in candidates:
        key = _base(a.name) or a.resource_id
        keep = by_base.get(key)
        if keep is None or a.posted > keep.posted:
            if keep is not None:
                keep.reason = f"older version of “{a.name}”"
            by_base[key] = a
        else:
            a.reason = f"older version of “{keep.name}”"

    # Within a tier, NEWEST first. Oldest-first (the original sort) kept the original
    # solicitation and Amend 1 while the file cap cut Amend 2-4 — i.e. the current versions.
    ranked = sorted(by_base.values(), key=lambda a: a.posted, reverse=True)
    ranked.sort(key=lambda a: _tier(a.name))  # stable: tier order, newest-first inside each
    total = 0
    for a in ranked:
        a.tier = _tier(a.name)
        if sum(1 for x in out if x.selected) >= MAX_FILES:
            a.reason = f"over the {MAX_FILES}-file cap"
        elif total + a.size > MAX_TOTAL_BYTES:
            a.reason = "over the package size cap"
        else:
            a.selected = True
            a.reason = ("defines the work (PWS/SOW)", "the solicitation / evaluation",
                        "supporting document")[a.tier]
            total += a.size
    return out


def fetch_solicitation_documents(
    notice_id: str, raw: Optional[list[dict]] = None,
) -> SolicitationDocuments:
    """List -> select -> parse -> digest one notice's documents into `document_text`.

    Pass `raw` (from `list_attachments`) when the caller already listed them, to avoid a
    second request. Never raises: an unreadable notice returns an empty result with `error`
    set, and the opportunity keeps its summary rather than failing ingestion.
    """
    from utils.doc_digest import digest_documents
    from utils.doc_parse import parse_document

    result = SolicitationDocuments()
    if raw is None:
        raw = list_attachments(notice_id)
    if raw is None:
        result.error = "listing failed"  # transient: the caller must NOT record a fingerprint
        return result
    result.fingerprint = fingerprint(raw)
    if not raw:
        result.error = "no attachments"
        return result

    atts = select_attachments(raw)
    texts: list[str] = []
    # Parse in priority order so, if anything fails, the PWS is the last thing to be lost.
    for a in sorted((x for x in atts if x.selected), key=lambda x: (x.tier, x.posted)):
        text = parse_document(a.download_url, max_chars=MAX_CHARS_PER_FILE, timeout=90.0)
        if not text:
            a.selected, a.reason = False, "could not be read"
            continue
        texts.append(f"===== FILE: {a.name} =====\n{text}")
        result.document_url = result.document_url or a.download_url
        result.parsed += 1

    result.attachments = [a.manifest() for a in atts]
    if not texts:
        result.error = "no readable documents"
        return result
    try:
        result.text = digest_documents(texts)
    except Exception as exc:  # noqa: BLE001 — the digest is the last step; keep the raw text
        logger.warning("SAM.gov digest failed for %s: %s", notice_id, exc)
        result.text = "\n\n".join(texts)[:MAX_CHARS_PER_FILE]
    return result
