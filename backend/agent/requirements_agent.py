"""The Requirements agent — reads a solicitation package and lists everything to submit.

Input is the ORIGINAL files, never the stored `document_text`: long packages are digested on
ingestion, and the digest drops exactly what this needs (the OASIS+ digest says page limits
"are not included"). See tasks/requirements_tasks.py for how the files are gathered.

The output is a draft for a person to approve, so it favours listing over deciding: when in
doubt an item stays on the list, flagged.
"""
from __future__ import annotations

import json
import logging
import re

from agno.agent import Agent

from agent.library_agent import _found, _tokens
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.requirements import Grounded, ProposalOutline
from utils.structured import run_structured

logger = logging.getLogger(__name__)


def _instructions() -> str:
    schema = json.dumps(ProposalOutline.model_json_schema(), separators=(",", ":"))
    return f"""\
You are a proposal compliance specialist for a government contractor. You are given every file
in ONE solicitation package. Work out exactly what must be submitted and how. A missed
requirement can disqualify the offer, so be complete and precise.

WHERE TO LOOK — every solicitation is different. The instructions may be a separate Section L
file, a section inside the RFP, an "Addendum to FAR 52.212-1", "Instructions to Offerors",
"Instructions for Preparation of Quotations", or a paragraph in an RFQ. Scoring may be Section M,
"Evaluation Factors" or FAR 52.212-2. Read all of it, including attachments, the PWS and any
Q&A file. If an amendment or Q&A answer changes an instruction, the newest one wins.

items — list EVERY separate thing the offeror must hand in. Do not assume a standard set of
volumes: use what THIS solicitation asks for, with its own names. Include, as separate items:
- each volume or quote part (kind "volume"; the price volume is kind "pricing");
- each plan required with the proposal, including draft plans/CDRLs required as appendices
  (kind "plan");
- resumes (kind "resume") and letters of commitment/intent (kind "letter");
- past-performance references and questionnaires (kind "past_performance");
- forms, signed amendments, SF forms, representations (kind "form");
- certifications or proof of licences (kind "certification");
- anything else required, such as staffing matrices (kind "attachment").
For each: its title as the solicitation names it, what it must contain, page_limit exactly as
stated including exclusions (with page_limit_quote, the exact words stating that limit), required
format, and source_ref (e.g. "L.3"). "No page limit" is a valid page_limit if the files say so.
Only list what is due WITH the offer. Deliverables due after award are NOT items, unless a draft
is required in the proposal.

pass_fail_rules — list every rule where missing it makes the offer ineligible, unawardable,
rejected or a "Deficiency" (e.g. "Standard for Acceptability", "Marginal in any subfactor is
ineligible", mandatory late-submission rules). Do not list ordinary scoring here.

submission, format_rules, evaluation, past_performance — fill what the files state. file_rules
must include any required email subject line or file-naming pattern, word for word, and any size
limit. page_counting must say exactly what counts toward limits: solicitations differ, so never
assume.

THE EVIDENCE RULE — checked by code:
- Every item and pass/fail rule needs `quote`: 5-30 words copied EXACTLY from the files, from one
  place. Do not join words from different paragraphs or bullet points.
- In submission, format_rules, evaluation and past_performance, every field you fill needs
  evidence[field] = the exact words it came from. Copy, never paraphrase.
- If a value is not stated, leave it null. Never guess, and never fill in what is typical.

If the files contain no submission instructions at all (e.g. only a PWS), set
found_instructions=false, say so in summary, and list only what the files clearly require.

Reply with ONLY a JSON object matching this schema (no prose, no code fences):
{schema}"""


def build_requirements_agent() -> Agent:
    return Agent(
        name="Requirements",
        # Low effort is what makes a big package finish at all: left to itself, GLM spent all
        # 40,000 output tokens thinking about OASIS+ and returned nothing. It's also ~10x faster.
        model=get_chat_llm_agno(model=settings.REQUIREMENTS_MODEL, max_tokens=30000,
                                reasoning_effort="low"),
        instructions=_instructions(),
    )


def read_requirements(title: str, files_text: str) -> ProposalOutline:
    """Read one package into a proposal outline, then check every quote against the files."""
    message = f"SOLICITATION: {title}\n\nFILES:\n{files_text}\n\nReturn the JSON."
    outline = run_structured(build_requirements_agent, message, ProposalOutline,
                             label=f"Requirements {title[:50]}")
    report = prove(outline, files_text)
    logger.info("Requirements %s: %d item(s), %d pass/fail rule(s), %s", title[:50],
                len(outline.items), len(outline.pass_fail_rules), report)
    return outline


# ---------------------------------------------------------------------------------------
# Proof gate
# ---------------------------------------------------------------------------------------
def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d+", s or ""))


def _quoted(quote: str | None, doc: str) -> bool:
    """Is this quote in the files? Exact first; then a tolerant check, because tables come out
    of the parser with their cells scrambled ("1 2 PRICE VOLUME No Page Limit") and a true quote
    can never match word for word. Most of its three-word runs must appear."""
    if not quote:
        return False
    if _found(quote, doc):
        return True
    words = _tokens(quote).split()
    if len(words) < 4:
        return False
    runs = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    return sum(f" {r} " in f" {doc} " for r in runs) / len(runs) >= 0.7


def _prove_sheet(sheet: Grounded, doc: str, flagged: list[str]) -> None:
    """Flag every fact whose quote can't be confirmed. It stays: a due date marked "check this"
    is safe, while a blank one reads as "no due date is stated" and can lose the bid."""
    proven: dict[str, str] = {}
    sheet.unverified = []
    for field, value in list(sheet):
        if field in ("evidence", "unverified") or value in (None, "", []):
            continue
        if isinstance(value, list):  # evaluation factors: judged through the basis
            continue
        quote = (sheet.evidence or {}).get(field)
        if _quoted(quote, doc):
            proven[field] = quote
        elif not _quoted(str(value), doc):  # no usable quote, but the value itself may be verbatim
            sheet.unverified.append(field)
            flagged.append(field)
    sheet.evidence = proven


def prove(outline: ProposalOutline, text: str) -> dict:
    """Apply the proof gate in place; returns what it flagged, for the logs."""
    doc = _tokens(text)
    flagged: list[str] = []
    for sheet in (outline.submission, outline.format_rules, outline.evaluation,
                  outline.past_performance):
        _prove_sheet(sheet, doc, flagged)

    for item in outline.items:
        item.verified = _quoted(item.quote, doc)
        # A page limit is the one number a whole volume is built on, so its digits must be in
        # its own confirmed quote; if not, the item is flagged for a person to check.
        if item.page_limit:
            q = item.page_limit_quote
            if not (_quoted(q, doc) and _numbers(item.page_limit) <= _numbers(q)):
                item.verified = False
        if not item.verified:
            flagged.append(item.title[:30])
    for rule in outline.pass_fail_rules:
        rule.verified = _quoted(rule.quote, doc)
        if not rule.verified:
            flagged.append(rule.rule[:30])
    outline.prohibited = [p for p in outline.prohibited if _quoted(p, doc)]
    return {"flagged": flagged}
