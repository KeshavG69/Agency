"""The Proposal Writer — drafts ONE required document from a solicitation's checklist.

One document per run, because each has its own rules (page limit, required content) and one
failing must not lose the rest. The output is structured sections, not a file: utils/docx_writer.py
renders them, so the solicitation's font and margins are applied by code rather than left to the
model.

THE RULE THAT MATTERS: a proposal states facts the government can hold the company to. So the
writer may only claim what the solicitation, the company profile or the library says. Anything it
doesn't have becomes a visible [NEEDS: ...] placeholder and a line in `gaps`, never a plausible
guess. Code backs this up: `unverified_numbers` lists every dollar figure and percentage in the
draft that can't be found in the source material, because invented results are the likeliest
fabrication and the hardest for a reviewer to spot.
"""
from __future__ import annotations

import json
import logging
import re

from agno.agent import Agent
from pydantic import BaseModel, Field

from agent.company_profile import company_context
from app.settings import settings
from client.library_store import get_library_store
from client.llm_client import get_chat_llm_agno
from utils.library_tools import library_tools
from utils.structured import run_structured

logger = logging.getLogger(__name__)

# Rough words per page in 12pt with one-inch margins, kept low so a draft lands under its limit.
_WORDS_PER_PAGE = 420
_DEFAULT_WORDS = 1200


class Table(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class Section(BaseModel):
    heading: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    table: Table | None = None


class WrittenDocument(BaseModel):
    title: str
    sections: list[Section]
    gaps: list[str] = Field(default_factory=list, description="Each thing missing, in plain words")


_GUIDE = {
    "past_performance": "Write each reference in the format the solicitation asks for. Use "
                        "find_past_performance_tool; use only contracts it returns.",
    "resume": "Use find_resumes_tool. One section per person. Never invent a person, a clearance "
              "or a certification.",
    "plan": "Write a real plan for THIS requirement: steps, roles, schedule and how it meets the "
            "PWS, not generic process text. Use search_library_tool for reusable material.",
    "letter": "Keep it short and formal. Leave the signature block for a person.",
}


_RFI_GUIDE = ("This is an RFI: the government wants information, not a proposal. Answer each of its "
              "questions in order, under its own number. Spend the effort on past performance: use "
              "find_past_performance_tool and lead with the contracts closest to this work. Be "
              "brief. Give no prices unless a question asks for them.")


def _page_budget(page_limit: str | None) -> tuple[int | None, int]:
    """(page limit, target words). The first number in the limit, unless it says there is none."""
    m = re.search(r"\d+", page_limit or "")
    if not m or re.search(r"\bno\b", page_limit or "", re.I):
        return None, _DEFAULT_WORDS
    pages = int(m.group())
    return pages, max(150, int(pages * _WORDS_PER_PAGE * 0.85))


def _instructions(company: str, profile: str) -> str:
    schema = json.dumps(WrittenDocument.model_json_schema(), separators=(",", ":"))
    return f"""\
You are a senior proposal writer for {company}, a government contractor. Draft ONE document for a
live bid. A human reviews it before it goes anywhere, but write it as if it were final.

COMPANY PROFILE:
{profile}

ABSOLUTE RULE: state only what is in the solicitation, the company profile, or the results of your
library tools. Never invent a contract, customer, person, clearance, certification, dollar figure,
percentage, metric or date. If you need something you don't have, write [NEEDS: what is missing]
in the text and add a plain line to `gaps`. A visible gap is a good outcome; a plausible invention
is a serious failure.

TOOLS: search the company library before writing. find_past_performance_tool for contracts,
find_resumes_tool for people, search_library_tool for reusable approach text.

HOW TO WRITE:
- Answer this document's requirements directly, in the order the solicitation gives them, using its
  headings and terms. Say how the company meets each one, not that it will.
- Be specific to this requirement. Reference PWS paragraphs where it helps the evaluator.
- Keep within the word budget: it is sized to the page limit, and pages over a limit are not read.
- No cover page or table of contents unless this document's requirements ask for one.
- Write only the proposal itself. Never mention formatting, fonts, page limits or word counts, and
  never describe the document ("this volume addresses…").

Reply with ONLY a JSON object matching this schema (no prose, no code fences):
{schema}"""


def write_document(organization_id: str, opp: dict, outline: dict, item: dict, source_text: str,
                   employee_email: str | None = None) -> WrittenDocument:
    """Draft one checklist item."""
    company, profile = company_context(organization_id)
    _, words = _page_budget(item.get("page_limit"))

    def build() -> Agent:
        return Agent(
            name="ProposalWriter",
            # The limit turns a hung call into a retry: left waiting, the line would sit on
            # "Writing…" with its Rewrite button disabled.
            model=get_chat_llm_agno(model=settings.WRITER_MODEL, max_tokens=24000,
                                    reasoning_effort="low", timeout=300),
            tools=library_tools(organization_id, employee_email),
            instructions=_instructions(company, profile),
        )

    guide = (_RFI_GUIDE if outline.get("solicitation_type") == "rfi"
             else _GUIDE.get(item.get("kind") or "", ""))
    message = (
        f"BID: {opp.get('title')} ({opp.get('solicitation_number') or 'no number'}), "
        f"{opp.get('agency') or 'agency not stated'}\n\n"
        f"DOCUMENT TO WRITE: {item.get('title')}\n"
        f"WHAT IT MUST CONTAIN: {item.get('detail') or 'see the solicitation'}\n"
        f"PAGE LIMIT: {item.get('page_limit') or 'none stated'} (write about {words} words)\n"
        f"WHERE IT IS ASKED FOR: {item.get('source_ref') or 'not stated'}\n"
        f"{guide}\n\n"
        f"HOW IT WILL BE SCORED: {json.dumps(outline.get('evaluation') or {})[:1500]}\n"
        f"RULES THAT DISQUALIFY AN OFFER: {json.dumps([p.get('rule') for p in outline.get('pass_fail_rules') or []])[:2500]}\n\n"
        f"SOLICITATION FILES:\n{source_text}\n\nReturn the JSON."
    )
    return run_structured(build, message, WrittenDocument, label=f"Writer {str(item.get('title'))[:40]}")


def document_text(doc: WrittenDocument) -> str:
    parts = [doc.title]
    for s in doc.sections:
        parts += [s.heading, *s.paragraphs, *s.bullets]
        if s.table:
            parts += s.table.headers + [c for r in s.table.rows for c in r]
    return "\n".join(p for p in parts if p)


_NUMBER = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:million|billion|[MBK]\b)?|\b\d+(?:\.\d+)?\s?%", re.I)


def _digits(s: str) -> str:
    return re.sub(r"[^\d.]", "", s).rstrip(".")


def unverified_numbers(doc: WrittenDocument, organization_id: str, source_text: str) -> list[str]:
    """Dollar figures and percentages in the draft that appear nowhere in the source material."""
    company, profile = company_context(organization_id)
    library = get_library_store().analysed(organization_id)
    known = " ".join([source_text, profile, json.dumps(library, default=str)])
    known_digits = {_digits(m) for m in _NUMBER.findall(known)}
    seen: dict[str, None] = {}
    for found in _NUMBER.findall(document_text(doc)):
        if _digits(found) not in known_digits:
            seen.setdefault(found.strip())
    return list(seen)


class SubmissionEmail(BaseModel):
    to: list[str] = Field(default_factory=list)
    subject: str = ""
    body: str = ""


def draft_email(organization_id: str, opp: dict, outline: dict, attachments: list[str],
                sender_name: str) -> SubmissionEmail:
    """The email that sends the finished package. A human reads it and sends it."""
    company, _ = company_context(organization_id)

    def build() -> Agent:
        return Agent(
            name="SubmissionEmail",
            model=get_chat_llm_agno(model=settings.REQUIREMENTS_MODEL, max_tokens=4000,
                                    reasoning_effort="low"),
            instructions=f"""\
Write the email that submits {company}'s response to a government solicitation.
- to: the submission address(es) from the rules. Only addresses given there; empty if none.
- subject: if the rules require a subject line, use it EXACTLY, filling in the company name.
  Otherwise use the solicitation number and title.
- body: short and formal. State what is being submitted, by solicitation number, and list the
  attached files by name. Sign off as {sender_name or "[Your Name]"}, {company}. No marketing, no
  claims about the company, nothing that isn't in the information given.
Reply with ONLY JSON: {{"to": ["..."], "subject": "...", "body": "..."}}""",
        )

    message = (
        f"SOLICITATION: {opp.get('solicitation_number')} — {opp.get('title')}\n"
        f"AGENCY: {opp.get('agency')}\n"
        f"SUBMISSION RULES: {json.dumps(outline.get('submission') or {})[:2500]}\n"
        f"ATTACHED FILES: {json.dumps(attachments)}"
    )
    return run_structured(build, message, SubmissionEmail, label="Submission email")
