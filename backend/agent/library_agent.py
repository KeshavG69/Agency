"""The Library agent — reads ONE library document, sorts it, and pulls out its key facts.

Runs once per SharePoint file (and again only when the file's content changes), so it is a
small model with no tools: everything it needs is the document text we hand it.

TWO RULES, the second backed by code rather than trust:
  1. One category per document, with an honest confidence. Low confidence is not a failure —
     the file goes to "Needs review" for a person instead of being guessed.
  2. NEVER INVENT A FACT. Every filled field must come with the exact words it was read from.
     `prove()` below checks each quote against the document text and drops any field whose
     quote isn't there. A fact that survives is one a proposal writer can cite.
"""
from __future__ import annotations

import json
import logging
import re

from agno.agent import Agent

from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.library import CATEGORY_LABELS, Grounded, LibraryAnalysis
from utils.structured import run_structured

logger = logging.getLogger(__name__)

# Below this the category is a suggestion for a person to confirm, not a fact.
REVIEW_BELOW = 0.6

_CATEGORY_GUIDE = """\
- past_performance: write-ups of contracts WE performed (project summaries, PP references, case studies)
- cpars: a government performance evaluation of our work (CPARS, PPQ, customer rating form)
- resume: one or more people's resumes / CVs / bios
- past_proposal: a proposal (or a volume of one) that WE submitted to a customer
- capture_plan: OUR internal pursuit strategy for an opportunity — capture plan, bid/no-bid
  analysis, win themes, competitor assessment, teaming strategy (never sent to the customer)
- capability_statement: a company one-pager / overview / line card
- technical_approach: reusable technical write-ups — methodology, architecture, solution approach
- management_plan: QA/QC plans, transition plans, staffing plans, org charts, risk plans
- pricing: rate sheets, price volumes, BOEs, labor-rate tables
- certification: ISO / CMMI / SAM / insurance / facility clearance / small-business certificates
- contract_legal: awarded contracts, task orders, teaming agreements, NDAs, subcontracts
- solicitation: an RFP / RFI / RFQ / sources-sought / PWS / SOW WRITTEN BY the government and
  RECEIVED by us. A document of ours that merely discusses a solicitation is NOT one.
- other: anything that fits none of the above (templates, marketing, internal admin, images)"""


def _instructions() -> str:
    schema = json.dumps(LibraryAnalysis.model_json_schema(), separators=(",", ":"))
    return f"""\
You catalogue a government contractor's document library. You are given ONE document
(its file name, folder path, and text). Work out what it is and pull out its key facts.

CATEGORIES (pick exactly one):
{_CATEGORY_GUIDE}

Use the folder path and file name as hints, but the CONTENT decides. A file named
"Final_v3.docx" in "Misc" that describes a contract we delivered is past_performance.
A solicitation we RECEIVED is "solicitation"; a proposal we WROTE is "past_proposal"; our own
strategy for winning one (win themes, bid/no-bid, competitors, teaming) is "capture_plan".

FACTS — fill only the sheets that fit the document:
- contracts: every contract we performed that the document describes (past_performance,
  cpars, and the past-performance section of a past_proposal). One entry per contract.
- people: one entry per resume.
- proposal: for a past_proposal.
- certifications: one entry per certificate / registration.
- legal: for contract_legal.

THE EVIDENCE RULE — this is checked by code, so follow it exactly:
- For EVERY field you fill, add `evidence[field] = "<exact words copied from the document>"`
  — a short verbatim span (5-25 words) that contains the value. Copy, never paraphrase.
- Write names, contract numbers, NAICS codes, certificate numbers and clearances EXACTLY as
  they appear in the document ("TS/SCI" stays "TS/SCI") — the value must be inside its quote.
- If the document does not state a value, leave the field null. Do NOT guess, infer or
  reformat a value into existence. A field without a matching quote is deleted.
- List fields (certifications, skills, parties) need no evidence entry, but every item must
  be written exactly as it appears in the document.

confidence: 0.9+ when the content is unmistakable, 0.6-0.9 when it fits but is mixed,
below 0.6 when you are unsure (a person will review it).

summary: 2-3 plain sentences — what the document is and what a proposal team would use it for.

Reply with ONLY a JSON object matching this schema (no prose, no code fences):
{schema}"""


def build_library_agent() -> Agent:
    return Agent(
        name="Library",
        model=get_chat_llm_agno(model=settings.LIBRARY_MODEL),
        instructions=_instructions(),
    )


def analyse_document(name: str, path: str, text: str, force_category: str | None = None) -> LibraryAnalysis:
    """Sort one document and extract its facts, then drop every fact the text doesn't back up.

    `force_category` is set when a person corrected the category: the model then fills the
    fact sheets for THAT category instead of choosing its own.
    """
    excerpt = text[: settings.LIBRARY_READ_CHARS]
    message = f"FILE NAME: {name}\nFOLDER PATH: {path}\n"
    if force_category:
        message += (
            f"\nA person has confirmed this document's category is "
            f"'{force_category}' ({CATEGORY_LABELS.get(force_category, force_category)}). "
            "Use exactly that category with confidence 1.0, and fill the matching fact sheets.\n"
        )
    if len(text) > len(excerpt):
        message += f"\n(The document is long; you are reading the first {len(excerpt):,} characters.)\n"
    message += f"\nDOCUMENT TEXT:\n{excerpt}\n\nReturn the JSON."

    result = run_structured(build_library_agent, message, LibraryAnalysis, label=f"Library {name}")
    if force_category:
        result.category = force_category  # type: ignore[assignment]
        result.confidence = 1.0
    dropped = prove(result, text)
    if dropped:
        logger.info("Library %s: dropped %d unproven fact(s): %s", name, len(dropped), ", ".join(dropped[:8]))
    return result


# ---------------------------------------------------------------------------------------
# Proof gate
# ---------------------------------------------------------------------------------------
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> str:
    """Words only, lower-cased, single-spaced — so markdown table pipes, bold markers, curly
    quotes and line breaks from the parser never make a real quote look invented."""
    return " ".join(_TOKEN.findall((s or "").lower()))


def _found(needle: str, haystack_tokens: str) -> bool:
    t = _tokens(needle)
    return bool(t) and f" {t} " in f" {haystack_tokens} "


# Identifiers must appear INSIDE their own quote — a real sentence next to an invented contract
# number would otherwise pass. (Free-text fields like `scope` are summaries, so they can't.)
_VALUE_IN_QUOTE = {"contract_number", "naics", "name", "number", "solicitation_number", "clearance"}


def _prove_sheet(sheet: Grounded, doc: str, label: str, dropped: list[str]) -> bool:
    """Null every scalar field whose evidence quote isn't in the document; keep only list
    items that appear verbatim. Returns True if anything of substance survived."""
    kept_any = False
    proven_evidence: dict[str, str] = {}
    for field, value in list(sheet):
        if field == "evidence" or value in (None, "", []):
            continue
        if isinstance(value, list):
            kept = [v for v in value if _found(str(v), doc)]
            if len(kept) < len(value):
                dropped.append(f"{label}.{field}[{len(value) - len(kept)}]")
            setattr(sheet, field, kept)
            kept_any = kept_any or bool(kept)
            continue
        quote = (sheet.evidence or {}).get(field)
        value_ok = field not in _VALUE_IN_QUOTE or _found(str(value), _tokens(quote or ""))
        if quote and value_ok and _found(quote, doc):
            proven_evidence[field] = quote
            kept_any = True
        else:
            dropped.append(f"{label}.{field}")
            setattr(sheet, field, None)
    sheet.evidence = proven_evidence
    return kept_any


def prove(result: LibraryAnalysis, text: str) -> list[str]:
    """Apply the proof gate in place. Returns the dropped field names (for logs)."""
    doc = _tokens(text)
    dropped: list[str] = []
    result.contracts = [c for i, c in enumerate(result.contracts) if _prove_sheet(c, doc, f"contract{i}", dropped)]
    result.people = [p for i, p in enumerate(result.people) if _prove_sheet(p, doc, f"person{i}", dropped)]
    result.certifications = [
        c for i, c in enumerate(result.certifications) if _prove_sheet(c, doc, f"cert{i}", dropped)
    ]
    if result.proposal and not _prove_sheet(result.proposal, doc, "proposal", dropped):
        result.proposal = None
    if result.legal and not _prove_sheet(result.legal, doc, "legal", dropped):
        result.legal = None
    result.confidence = max(0.0, min(1.0, float(result.confidence or 0)))
    return dropped
