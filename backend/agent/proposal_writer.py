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
from pydantic import BaseModel, Field, PrivateAttr

from agent.company_profile import company_context
from app.settings import settings
from client.library_store import get_library_store
from client.llm_client import get_chat_llm_agno
from utils.docx_writer import page_total
from utils.library_tools import library_tools
from utils.structured import run_structured

logger = logging.getLogger(__name__)

# Rough words per page in 12pt with one-inch margins, kept low so a draft lands under its limit.
_WORDS_PER_PAGE = 420
_DEFAULT_WORDS = 1200


class Table(BaseModel):
    caption: str = Field("", description="What the table shows, in a few words")
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class Cited(BaseModel):
    """One claim about the company's own work, with the words in a library file that prove it.
    Code checks the quote against that one file; a claim it can't find is flagged, not kept."""

    text: str = Field(description="The claim, as it should read in the proposal")
    source_id: str = Field("", description="`id` of the library file the quote comes from")
    quote: str = Field("", description="5-30 words copied EXACTLY from that file")


class WorkRow(BaseModel):
    work: Cited = Field(description="Something we did on THIS contract, proven from its own file")
    relevance: str = Field(description="Why that matters for this bid, in the solicitation's terms")


class Reference(BaseModel):
    """A past contract. Its number, customer, value, dates, role and contact are filled in by
    code from the library record, so only the narrative is written here."""

    contract_number: str = Field(description="Exactly as find_past_performance_tool returned it")
    scope: Cited = Field(description="1-2 sentences: what we delivered on this contract")
    work: list[WorkRow] = Field(default_factory=list)
    results: list[Cited] = Field(default_factory=list, description="Outcomes and metrics, if the file states any")
    _facts: list = PrivateAttr(default_factory=list)


class Requirement(BaseModel):
    requirement: str = Field(description="A requirement for this position, from the solicitation")
    experience: Cited = Field(description="This person's experience that meets it, from their resume")


class PersonBio(BaseModel):
    """A proposed person. Clearance, certifications, education, years and current role are
    filled in by code from their resume record, so only what they'd do and have done is here."""

    name: str = Field(description="Exactly as find_resumes_tool returned it")
    proposed_position: str = Field(description="The position in this bid, as the solicitation names it")
    summary: list[Cited] = Field(default_factory=list)
    employment: list[Cited] = Field(default_factory=list, description="Each job as 'Employer, title, dates'")
    requirements: list[Requirement] = Field(default_factory=list)
    _facts: list = PrivateAttr(default_factory=list)


class Section(BaseModel):
    heading: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    table: Table | None = None
    references: list[Reference] = Field(default_factory=list)
    people: list[PersonBio] = Field(default_factory=list)


class WrittenDocument(BaseModel):
    title: str
    sections: list[Section]
    assumptions: list[str] = Field(default_factory=list, description="What you assumed where the "
                                   "solicitation is silent, one per line")
    questions: list[str] = Field(default_factory=list, description="What to ask the contracting officer")
    commitments: list[str] = Field(default_factory=list, description="Every specific promise you made "
                                   "that no source states: schedules, reviews, staffing, targets")
    gaps: list[str] = Field(default_factory=list, description="Each company fact missing, in plain words")
    disclosure_marking: str = Field(default="", description=(
        "The marking printed in every page footer, or \"\" for none. You decide from the "
        "solicitation. Use one (usually \"Proprietary Information\", or the exact wording the "
        "solicitation requires) only when this document carries the company's own non-public data "
        "AND the solicitation does not forbid proprietary information. Many RFIs and sources-sought "
        "notices say not to submit proprietary information: then leave it empty. A non-empty "
        "marking also puts the standard restrictive-use legend on the cover."))


_GUIDE = {
    "past_performance": "Put each contract in `references` (in a section), in the order the "
                        "solicitation asks. Use only contracts find_past_performance_tool returns.",
    "resume": "Put each person in `people` (in a section). Use only people find_resumes_tool returns.",
    "plan": "Write a real plan for THIS requirement: steps, roles, schedule and how it meets the "
            "PWS, not generic process text. Use search_library_tool for reusable material.",
    "letter": "Keep it short and formal. Leave the signature block for a person.",
}


_RFI_GUIDE = ("This is an RFI: the government wants information, not a proposal. Answer each of its "
              "questions in order, under its own number. Spend the effort on past performance: use "
              "find_past_performance_tool, lead with the contracts closest to this work, and put "
              "them in `references`. Be brief. Give no prices unless a question asks for them.")


def _page_budget(page_limit: str | None) -> tuple[int | None, int]:
    """(page limit, target words)."""
    pages = page_total(page_limit)
    if pages is None:
        return None, _DEFAULT_WORDS
    return pages, max(150, int(pages * _WORDS_PER_PAGE * 0.85))


def _instructions(company: str, profile: str) -> str:
    schema = json.dumps(WrittenDocument.model_json_schema(), separators=(",", ":"))
    return f"""\
You are a senior proposal writer for {company}, a government contractor. Draft ONE document for a
live bid. A person reviews it before it goes anywhere, but write it as if it were final.

COMPANY PROFILE:
{profile}

EVERY FACT ABOUT THE COMPANY MUST COME FROM ITS LIBRARY, AND CODE CHECKS IT:
- Search first: find_past_performance_tool for contracts, find_resumes_tool for people,
  search_library_tool for approach text. Each result carries the `id` of its file.
- Past contracts go in `references` and people in `people`. Give only the contract number or the
  person's name exactly as the tool returned it: their numbers, customers, values, dates, contacts,
  clearances, certifications and education are filled in from the library by code. Never type them.
- Each claim about work the company or a person did is a `Cited`: the claim, the `id` of the file
  it comes from, and 5-30 words copied exactly from THAT file. A claim about one contract must be
  proven from that contract's own file, not another one.
- In ordinary paragraphs, put [[file id|exact words from it]] right after any sentence that states
  a company fact (a past result, a customer, a number, a tool used before). Code deletes the marker
  before anyone reads the document, so the sentence must say everything without it: the marker is
  proof, never part of the wording. Sentences stating a company fact without one are flagged.
- Never type a contract number, phone number, email or dollar figure into a paragraph or table of
  your own: those belong in `references` and `people`, where code fills them in.
- Never invent a contract, customer, person, clearance, certification, figure or date. A company
  fact you don't have is [NEEDS: what is missing] in the text plus a line in `gaps`.

WHAT THE GOVERNMENT DIDN'T SAY (hours, volumes, systems, service levels): don't write a gap or say
it is "not stated". Write the approach under a stated assumption and add that assumption to
`assumptions`; add anything worth asking to `questions`. Never promise to meet service levels or
targets the solicitation doesn't define.

PROMISES: approach text may say what the company will do. List every specific promise no source
states (a schedule, a review cadence, a staffing level, a target) in `commitments`, so a person
approves it.

HOW IT SHOULD READ — like a seasoned proposal manager wrote it, not a chatbot:
- Answer the requirements in the solicitation's order, with its headings, numbering and terms.
- Short, specific, factual sentences. Name the PWS task, the system, the deliverable.
- Use tables where an evaluator compares things (tasks, roles, schedule, staffing).
- Never use: robust, seamless, leverage, cutting-edge, state-of-the-art, best-in-class,
  world-class, holistic, synergy, unparalleled, "comprehensive solution", "we are excited".
- Write only the proposal. Never describe the document ("this volume describes…"), never mention
  the library, "our records" or "our files", a resume or a source ("per the resume"), formatting or
  word counts, and never offer contacts or details "on request": give them, or mark them [NEEDS: …].
- Never argue against the company. Don't write what it lacks, hasn't shown, won't claim or can't
  cite ("we have not yet demonstrated…", "Nexagen does not claim…", "no contract is available").
  Say what you can prove; a capability you can't prove is a [NEEDS: evidence of …] marker and a
  line in `gaps`. Where the solicitation asks for a yes/no/possibly answer with its basis, give the
  factors that decide it, not a confession.
- No cover page, contents page or legal notice in the text: code adds those. Whether the
  document carries a disclosure marking at all is yours to decide (`disclosure_marking`).
- Stay within the word budget: it is sized to the page limit.

Reply with ONLY a JSON object matching this schema (no prose, no code fences):
{schema}"""


# ---- the writer's memory of each document --------------------------------------------------
# Every document has its own Agno session, so a rewrite starts from what this writer did last
# time: its brief and the draft it returned. Only that is remembered. The solicitation rides in
# the system message, which history leaves out (it is resent fresh each run, never twice), and
# tool calls aren't stored, so the memory is the conversation, not pages of library results.
_SESSION_DB = None
_KEEP_RUNS = 2


def _session_db():
    global _SESSION_DB
    if _SESSION_DB is None:
        from agno.db.mongo import MongoDb

        from client.mongo import mongo_client

        # The process's shared client: a `db_url` would open another one, threads and all.
        _SESSION_DB = MongoDb(db_client=mongo_client(), db_name=settings.MONGODB_DATABASE,
                              session_collection="writer_sessions")
    return _SESSION_DB


def writer_session_id(opportunity_id: str, item_id: str) -> str:
    return f"writer-{opportunity_id}-{item_id}"


def _trim_session(session_id: str) -> None:
    """Keep the stored session small: the last runs only, without their system message (the
    solicitation, up to 160k characters, which history never reads back)."""
    from agno.db.base import SessionType

    try:
        db = _session_db()
        session = db.get_session(session_id=session_id, session_type=SessionType.AGENT)
        if not session or not session.runs:
            return
        session.runs = session.runs[-_KEEP_RUNS:]
        for run in session.runs:
            run.messages = [m for m in run.messages or [] if m.role != "system"]
        db.upsert_session(session)
    except Exception as exc:  # noqa: BLE001 — a fat session is a cost, not a failure
        logger.warning("Writer session %s: couldn't trim (%s)", session_id, exc)


def notes_text(notes: list[dict] | None) -> str:
    return "\n".join(f"- {n.get('text', '').strip()}" for n in notes or [] if n.get("text"))


def write_document(organization_id: str, opp: dict, outline: dict, item: dict, source_text: str,
                   employee_email: str | None = None, *, session_id: str | None = None,
                   rewrite: bool = False) -> WrittenDocument:
    """Draft one checklist item, or rewrite it from the last draft, the problems the checks found
    in it and what the person has told the writer since."""
    company, profile = company_context(organization_id)
    _, words = _page_budget(item.get("page_limit"))
    notes = notes_text(item.get("writer_notes"))

    from agent.capability_agent import matrix_brief
    from client.capability_store import get_capability_store

    matrix = matrix_brief(get_capability_store().get(organization_id, str(opp.get("id") or opp.get("_id") or "")))
    context = (
        (f"{matrix}\n\n" if matrix else "") +
        f"SOLICITATION FILES:\n{source_text}\n\n"
        f"HOW IT WILL BE SCORED: {json.dumps(outline.get('evaluation') or {})[:1500]}\n"
        f"RULES THAT DISQUALIFY AN OFFER: {json.dumps([p.get('rule') for p in outline.get('pass_fail_rules') or []])[:2500]}\n"
        f"WHAT NOT TO SUBMIT: {json.dumps(outline.get('prohibited') or [])[:1500]}"
    )

    def build() -> Agent:
        memory = dict(db=_session_db(), session_id=session_id, user_id=organization_id,
                      add_history_to_context=True, num_history_runs=1,
                      store_tool_messages=False) if session_id else {}
        return Agent(
            name="ProposalWriter",
            # The limit turns a hung call into a retry: left waiting, the line would sit on
            # "Writing…" with its Rewrite button disabled.
            model=get_chat_llm_agno(model=settings.WRITER_MODEL, max_tokens=24000,
                                    reasoning_effort="low", timeout=300),
            tools=library_tools(organization_id, employee_email),
            instructions=_instructions(company, profile),
            additional_context=context,
            **memory,
        )

    guide = (_RFI_GUIDE if outline.get("solicitation_type") == "rfi"
             else _GUIDE.get(item.get("kind") or "", ""))
    brief = (
        f"BID: {opp.get('title')} ({opp.get('solicitation_number') or 'no number'}), "
        f"{opp.get('agency') or 'agency not stated'}\n\n"
        f"DOCUMENT TO WRITE: {item.get('title')}\n"
        f"WHAT IT MUST CONTAIN: {item.get('detail') or 'see the solicitation'}\n"
        f"PAGE LIMIT: {item.get('page_limit') or 'none stated'} (write about {words} words)\n"
        f"WHERE IT IS ASKED FOR: {item.get('source_ref') or 'not stated'}\n"
        f"{guide}"
    )
    told = ""
    if notes:
        told = (
            "\n\nWHAT THE PERSON TOLD YOU (source id `notes`) — answers from the contracting "
            "officer, corrections, facts about the company. Treat them as true and use them: they "
            "replace any assumption or question they answer (drop those from `assumptions` and "
            "`questions`). Cite them like a file: [[notes|exact words from the note]].\n" + notes
        )
    if rewrite:
        flagged = "\n".join(f"- {g}" for g in (item.get("gaps") or [])[:40]) or "- nothing flagged"
        message = (
            "REWRITE this document. Your last draft is above (if you can't see it, write it fresh "
            "from the brief below). Keep what was right; fix what the checks found:\n"
            f"{flagged}{told}\n\nTHE BRIEF:\n{brief}\n\nReturn the whole document as JSON."
        )
    else:
        message = f"{brief}{told}\n\nReturn the JSON."
    doc = run_structured(build, message, WrittenDocument, label=f"Writer {str(item.get('title'))[:40]}")
    if session_id:
        _trim_session(session_id)
    return doc


def document_text(doc: WrittenDocument) -> str:
    """Every word the model wrote, for the checks and the page estimate."""
    parts = [doc.title, *doc.assumptions]
    for s in doc.sections:
        parts += [s.heading, *s.paragraphs, *s.bullets]
        if s.table:
            parts += [s.table.caption, *s.table.headers] + [c for r in s.table.rows for c in r]
        for r in s.references:
            parts += [r.scope.text] + [x for w in r.work for x in (w.work.text, w.relevance)]
            parts += [c.text for c in r.results]
        for p in s.people:
            parts += [p.proposed_position, *(c.text for c in p.summary + p.employment)]
            parts += [x for q in p.requirements for x in (q.requirement, q.experience.text)]
    return "\n".join(x for x in parts if x)


_NUMBER = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?(?:million|billion|[MBK]\b)?|\b\d+(?:\.\d+)?\s?%", re.I)
# Identifiers a reviewer can't eyeball: phone numbers, emails, contract and order numbers.
_IDENT = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"
                    r"|[\w.+-]+@[\w-]+\.[\w.]+"
                    r"|\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]{2,}(?:-[A-Z0-9]{1,}){1,4}\b"
                    r"|\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,}\b")


def _digits(s: str) -> str:
    return re.sub(r"[^\d.]", "", s).rstrip(".")


def unverified_numbers(doc: WrittenDocument, organization_id: str, source_text: str) -> list[str]:
    """Dollar figures, percentages, phone numbers, emails and contract numbers in the model's own
    words that appear nowhere in the source material (code-filled table facts aren't checked
    here: they are copied from the library)."""
    company, profile = company_context(organization_id)
    library = get_library_store().analysed(organization_id)
    known = " ".join([source_text, profile, json.dumps(library, default=str)])
    known_digits = {_digits(m) for m in _NUMBER.findall(known)}
    seen: dict[str, None] = {}
    text = document_text(doc)
    for found in _NUMBER.findall(text):
        if _digits(found) not in known_digits:
            seen.setdefault(found.strip())
    plain = re.sub(r"[^a-z0-9@.]", "", known.lower())
    for found in _IDENT.findall(text):
        if re.sub(r"[^a-z0-9@.]", "", found.lower()) not in plain:
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
