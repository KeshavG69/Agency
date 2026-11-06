"""The Capability agent — maps what a solicitation asks for to what our library proves we can do.

Two passes:
  1. Extract: read the solicitation, list the areas of work and their requirements (each with the
     exact words it came from), and the government contacts it names.
  2. Score: for each requirement, CODE searches the library (contracts, people, passages); one
     model call per area picks the evidence and proposes a score from 1 to 5.

Then code decides. Every requirement quote is checked against the solicitation, every piece of
evidence against its own library file (a contract against its own part of the file), and the
score is capped by what the proven evidence allows — RUBRIC in models/capability.py. A model can
lower a score but never lift one past its proof, so a 5 always has a recent contract behind it.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from agno.agent import Agent

from agent.library_agent import _tokens
from agent.proposal_grounding import _Grounder, _norm
from agent.requirements_agent import _quoted
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.capability import (
    RUBRIC,
    AreaScores,
    CapArea,
    CapRequirement,
    Evidence,
    Extraction,
)
from utils.structured import run_structured

logger = logging.getLogger(__name__)

_MAX_REQUIREMENTS = 40
_RECENT_YEARS = 3
# Our own drafts can end up in the library; their review markers give them away, and a draft
# Collecct wrote is never proof of work the company did.
_DRAFT = re.compile(r"\[(?:CHECK|NEEDS):")


# ---- pass 1 -------------------------------------------------------------------------------
def _extract_instructions() -> str:
    schema = json.dumps(Extraction.model_json_schema(), separators=(",", ":"))
    return f"""\
You are a capture manager for a government contractor. From ONE solicitation package, list what
the government wants the contractor to DO and to HAVE, so the company can rate itself against it.

areas — group the work into 3-8 areas, named the way the solicitation names them (e.g. "Service
Desk", "Cybersecurity", "Program Management"). Under each, list its requirements: the tasks in the
PWS/SOW/Statement of Objectives, and the qualifications the contractor must have (key personnel
qualifications, clearances, certifications such as CMMI, ISO or FedRAMP, required tools, required
experience). One requirement per line, in plain words. At most {_MAX_REQUIREMENTS} in total: merge
small related tasks rather than dropping them.

Do NOT list submission instructions (page limits, volumes, forms, due dates): those are handled
elsewhere. Do NOT list contract terms the company can't be stronger or weaker at: period of
performance, option periods, contract type, place of performance, clauses, invoicing or other
administrative rules.

contacts — every government person the files name for this solicitation: contracting officer,
contract specialist, COR, with email and phone exactly as written. Never invent one.

THE EVIDENCE RULE — checked by code: every requirement and contact needs `quote`: 5-30 words
copied EXACTLY from the files, from one place. Copy, never paraphrase.

Reply with ONLY a JSON object matching this schema (no prose, no code fences):
{schema}"""


def extract(title: str, source_text: str) -> Extraction:
    def build() -> Agent:
        return Agent(
            name="CapabilityExtract",
            model=get_chat_llm_agno(model=settings.REQUIREMENTS_MODEL, max_tokens=16000,
                                    reasoning_effort="low"),
            instructions=_extract_instructions(),
        )

    message = f"SOLICITATION: {title}\n\nFILES:\n{source_text}\n\nReturn the JSON."
    return run_structured(build, message, Extraction, label=f"Capability extract {title[:40]}")


# ---- candidates: what the library holds for each requirement -------------------------------
def _year(s: str | None) -> int | None:
    m = re.findall(r"(?:19|20)\d{2}", s or "")
    return int(m[-1]) if m else None


def _recent(contract: dict, category: str | None) -> bool:
    if category == "active_contract":
        return True
    end = _year(contract.get("end_date"))
    return bool(end and end >= date.today().year - _RECENT_YEARS)


class _Candidates:
    """The library results for one area, each under a short id (C1, P1, D1) so the model never
    types a file id, and with only the text it may quote: verbatim lines from the file."""

    def __init__(self, organization_id: str, employee_email: str | None):
        self.org = organization_id
        self.email = employee_email
        self.by_id: dict[str, dict] = {}
        self._keys: dict[tuple, str] = {}

    def _add(self, kind: str, key: tuple, entry: dict) -> str:
        if key in self._keys:
            return self._keys[key]
        prefix = {"contract": "C", "person": "P", "document": "D"}[kind]
        cid = f"{prefix}{sum(1 for k in self._keys if k[0] == kind) + 1}"
        self._keys[key] = cid
        self.by_id[cid] = {"id": cid, "kind": kind, **entry}
        return cid

    def find(self, requirement: str) -> list[str]:
        from client.library_graph import search_passages
        from utils.library_tools import find_past_performance, find_resumes, generated_item_ids

        ids = []
        for c in find_past_performance(requirement, self.org, self.email, limit=3):
            src = c.get("source") or {}
            quotable = [v for v in (c.get("evidence") or {}).values() if v]
            if not quotable:
                continue
            ids.append(self._add("contract", ("contract", src.get("id"), c.get("contract_number") or c.get("title")), {
                "name": " — ".join(x for x in (c.get("contract_number"), c.get("title")) if x),
                "item_id": src.get("id"), "file": src.get("file"), "web_url": src.get("web_url"),
                "contract_number": c.get("contract_number"), "title": c.get("title"),
                "customer": c.get("customer") or c.get("agency"),
                "recent": _recent(c, src.get("category")), "quotable": quotable,
            }))
        for p in find_resumes(requirement, self.org, self.email, limit=2):
            src = p.get("source") or {}
            quotable = [v for v in (p.get("evidence") or {}).values() if v]
            if not quotable:
                continue
            ids.append(self._add("person", ("person", src.get("id"), p.get("name")), {
                "name": p.get("name"), "role": p.get("current_role"), "item_id": src.get("id"),
                "file": src.get("file"), "web_url": src.get("web_url"), "quotable": quotable,
            }))
        for d in search_passages(requirement, self.org, self.email, k=3):
            text = (d.get("text") or "").strip()
            if (not text or _DRAFT.search(text) or d.get("category") == "solicitation"
                    or d.get("id") in generated_item_ids()):
                continue
            ids.append(self._add("document", ("document", d.get("id"), text[:80]), {
                "name": d.get("name"), "item_id": d.get("id"), "file": d.get("name"),
                "web_url": d.get("web_url"), "category": d.get("category"),
                "quotable": [text[:900]],
            }))
        return ids

    def for_prompt(self) -> str:
        shown = []
        for c in self.by_id.values():
            row = {"id": c["id"], "kind": c["kind"], "name": c.get("name"), "file": c.get("file"),
                   "quotable_text": c["quotable"]}
            if c["kind"] == "contract":
                row.update(customer=c.get("customer"), recent=c.get("recent"))
            if c["kind"] == "person":
                row["role"] = c.get("role")
            shown.append(row)
        return json.dumps(shown, default=str)


# ---- pass 2 -------------------------------------------------------------------------------
def _score_instructions(company: str) -> str:
    schema = json.dumps(AreaScores.model_json_schema(), separators=(",", ":"))
    rubric = "\n".join(f"  {k}: {v}" for k, v in sorted(RUBRIC.items(), reverse=True))
    return f"""\
You rate {company} against the requirements of a government solicitation, using ONLY the library
results given. Be strict: an evaluator will check every claim.

For each requirement (by its `index`):
- evidence: the results that show we can do it. For each, `source_id` is the result's `id`,
  `quote` is 5-30 words copied EXACTLY from that result's `quotable_text` (nowhere else), `claim`
  restates ONLY what the quote says, in the quote's own words (no judgment, no "relevant to…",
  nothing about this solicitation: that goes in `rationale`), and `match` is "same" only if it is
  this exact work, otherwise "related". Use no result that doesn't fit. No evidence is a valid answer.
- score, by this rubric:
{rubric}
- rationale: why, in one or two plain sentences.
- gap and action, when the score is 1 or 2: what is missing, and how to close it (a teaming
  partner, a hire, a resume to add, a question for the contracting officer).

Code checks every quote and caps the score at what the proven evidence supports.

Reply with ONLY a JSON object matching this schema (no prose, no code fences):
{schema}"""


def _score_area(area, reqs: list[CapRequirement], cands: _Candidates, wanted: list[list[str]],
                company: str) -> AreaScores:
    def build() -> Agent:
        return Agent(
            name="CapabilityScore",
            model=get_chat_llm_agno(model=settings.REQUIREMENTS_MODEL, max_tokens=12000,
                                    reasoning_effort="low"),
            instructions=_score_instructions(company),
        )

    lines = [f"{i}. {r.text}" + (f" (results: {', '.join(w)})" if w else " (no library results)")
             for i, (r, w) in enumerate(zip(reqs, wanted), 1)]
    message = (f"AREA: {area}\n\nREQUIREMENTS:\n" + "\n".join(lines) +
               f"\n\nLIBRARY RESULTS:\n{cands.for_prompt()}\n\nReturn the JSON.")
    return run_structured(build, message, AreaScores, label=f"Capability score {area[:30]}")


# ---- proof --------------------------------------------------------------------------------
def ceiling(evidence: list[Evidence], cands: _Candidates) -> int:
    """The highest score the PROVEN evidence allows (RUBRIC)."""
    top = 1
    for e in evidence:
        if not e.verified:
            continue
        c = cands.by_id.get(e.source_id) or {}
        if e.kind == "contract":
            top = max(top, (5 if c.get("recent") else 4) if e.match == "same" else 3)
        elif e.kind == "document":
            top = max(top, 3 if e.match == "same" else 2)
        else:  # a person shows we have the skill, not that the company did the work
            top = max(top, 2)
    return top


def _prove(e: Evidence, cands: _Candidates, g: _Grounder) -> None:
    c = cands.by_id.get(e.source_id)
    if not c or not c.get("item_id"):
        e.verified = False
        return
    e.kind = c["kind"]  # what the result IS, not what the model called it
    e.name, e.file, e.web_url = c.get("name") or e.name, c.get("file"), c.get("web_url")
    item_id = c["item_id"]
    part = None
    row = g.rows.get(item_id) or {}
    if e.kind == "contract":
        mine = _norm(c.get("contract_number")) or _norm(c.get("title"))
        others = [x for o in row.get("contracts") or []
                  if (_norm(o.get("contract_number")) or _norm(o.get("title"))) != mine
                  for x in (o.get("title"), o.get("contract_number"))]
        part = g._part(item_id, [c.get("title"), c.get("contract_number")], others)
    elif e.kind == "person":
        others = [o.get("name") for o in row.get("people") or [] if _norm(o.get("name")) != _norm(c.get("name"))]
        part = g._part(item_id, [c.get("name")], others)
    e.verified = g.proven(e.claim, item_id, e.quote, {item_id}, part)


# ---- the whole thing ----------------------------------------------------------------------
def build_matrix(organization_id: str, opp: dict, source_text: str, company: str,
                 employee_email: str | None = None) -> tuple[list[CapArea], list[dict], dict]:
    """(areas, contacts, stats). `employee_email=None` reads the whole library: the automatic run
    after a reading has no person behind it."""
    title = opp.get("title") or "this solicitation"
    found = extract(title, source_text)
    doc = _tokens(source_text)

    areas: list[CapArea] = []
    total = 0
    for a in found.areas:
        reqs = []
        for r in a.requirements:
            if total >= _MAX_REQUIREMENTS or not r.text.strip():
                break
            reqs.append(CapRequirement(id=uuid.uuid4().hex[:10], text=r.text.strip(),
                                       source_ref=r.source_ref, quote=r.quote,
                                       verified=_quoted(r.quote, doc)))
            total += 1
        if reqs:
            areas.append(CapArea(name=a.name.strip() or "Other", requirements=reqs))
    contacts = []
    for c in found.contacts:
        c.verified = _quoted(c.quote, doc) or _quoted(c.name, doc)
        contacts.append(c.model_dump())

    g = _Grounder(organization_id, employee_email, source_text, company)

    def one(area: CapArea) -> None:
        cands = _Candidates(organization_id, employee_email)
        wanted = [cands.find(r.text) for r in area.requirements]
        try:
            scored = _score_area(area.name, area.requirements, cands, wanted, company)
        except Exception as exc:  # noqa: BLE001 — one area failing leaves the rest
            logger.warning("Capability: scoring %s failed: %s", area.name, exc)
            for r in area.requirements:
                r.rationale = "Couldn't score this area. Rebuild to try again."
                r.gap = r.gap or "Not scored."
            return
        by_index = {s.index: s for s in scored.requirements}
        for i, r in enumerate(area.requirements, 1):
            s = by_index.get(i)
            if s is None:
                r.rationale = "Not scored."
                r.gap = "Not scored. Rebuild to try again."
                continue
            for e in s.evidence:
                _prove(e, cands, g)
            r.evidence = s.evidence
            r.model_score = s.score
            r.score = max(1, min(s.score, ceiling(s.evidence, cands)))
            r.rationale = s.rationale
            if r.score <= 2:
                r.gap = s.gap or "No proof in your library for this."
                r.action = s.action
            if r.model_score > r.score:
                r.rationale += f" (Rated {r.model_score}, lowered to {r.score}: the proof found only supports {r.score}.)"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, areas))

    reqs = [r for a in areas for r in a.requirements]
    from client.library_store import get_library_store
    from utils.library_tools import generated_item_ids

    library = [r for r in get_library_store().analysed(organization_id, email=employee_email)
               if r.get("item_id") not in generated_item_ids() and r.get("category") != "solicitation"]
    stats = {
        "library_files": len(library),
        "requirements": len(reqs),
        "gaps": sum(1 for r in reqs if r.score <= 2),
        "strong": sum(1 for r in reqs if r.score >= 4),
        "average": round(sum(r.score for r in reqs) / len(reqs), 1) if reqs else None,
        "unverified_quotes": sum(1 for r in reqs if not r.verified),
        "evidence_rejected": sum(1 for r in reqs for e in r.evidence if not e.verified),
    }
    logger.info("Capability %s: %s", title[:40], stats)
    return areas, contacts, stats


def matrix_brief(matrix: dict | None, limit: int = 12) -> str:
    """What the writer should lean on and what it must not claim, from a ready matrix."""
    if not matrix or matrix.get("status") != "ready":
        return ""
    reqs = [(a["name"], r) for a in matrix.get("areas") or [] for r in a.get("requirements") or []]
    strong = [f"- {n}: {r['text']} ({r['score']}/5; " +
              ", ".join(e["name"] for e in r.get("evidence") or [] if e.get("verified"))[:120] + ")"
              for n, r in reqs if r.get("score", 0) >= 4][:limit]
    weak = [f"- {n}: {r['text']} — {r.get('gap') or 'no proof'}" for n, r in reqs if r.get("score", 5) <= 2][:limit]
    if not strong and not weak:
        return ""
    return ("CAPABILITY MATRIX (proven against the library):\n"
            + ("OUR STRENGTHS — lead with these:\n" + "\n".join(strong) + "\n" if strong else "")
            + ("OUR GAPS — never claim these; write the approach and flag [NEEDS: …]:\n" + "\n".join(weak) if weak else ""))
