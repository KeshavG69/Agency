"""The Analyst Agent.

Judges a SINGLE government opportunity for the company — bid / no-bid, priority, and
(if worth pursuing) the call-plan entry. One agent per opportunity; the Celery
layer fans these out in parallel and writes the verdicts back to the CRM store.

Tools: Exa web search (to research agency / incumbent / recent awards for
winnability) + a reasoning tool. The opportunity + the company profile (built per-org
from its UEI) are fed into the context; the agent returns a structured AnalystVerdict.
"""
from __future__ import annotations

import logging
from datetime import datetime

from agno.agent import Agent

from agent.company_profile import company_context
from app.settings import settings
from client.llm_client import get_chat_llm_agno
from models.verdict import AnalystVerdict
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.doc_parse import document_context
from utils.structured import coerce_output

logger = logging.getLogger(__name__)


def _instructions(company: str, profile: str) -> str:
    return f"""\
You are a SENIOR government-contracting (govcon) capture / business-development analyst for
{company}. You receive ONE opportunity — its metadata AND, when provided, the full solicitation
documents. Decide whether {company} should pursue it: Bid, Watch, or No-Bid, and score priority.

COMPANY PROFILE (your "fit" lens):
{profile}

STEP 1 — MINE THE SOLICITATION (do NOT rely on the sparse metadata alone; the real facts are in
the documents). Pull the decision-critical facts:
- Contract VEHICLE / type: is this a standalone solicitation, or a TASK ORDER under an IDIQ /
  GWAC / BPA (e.g. CECOM RS3, SeaPort-NxG, OASIS+, GSA MAS, Alliant)? Single- or multiple-award?
- Set-aside / competition type and the eligibility it requires.
- NAICS / PSC and the ACTUAL scope of work.
- Required clearances — personnel AND facility (e.g. SECRET Facility Clearance), plus mandated
  certifications/systems (ISO 9001, CMMI, DCAA-auditable accounting).
- Incumbent / recompete signals (a "recompete" PWS means there IS an incumbent).
- Evaluation approach (LPTA vs best-value tradeoff; factor weighting).
- Response deadline and estimated value — search the documents (Section L / cover), not just the
  metadata, which is often incomplete.

STEP 2 — DECIDE using this framework. The key move: NOT every gap is a No-Bid. Separate them.
(A) HARD DISQUALIFIERS — things you can determine are DEFINITELY absent/wrong from the profile +
    documents: a set-aside {company} is plainly INELIGIBLE for (with no teaming path); NAICS or
    scope clearly OUTSIDE {company}'s lane; a mandatory qualification the profile shows {company}
    lacks with no path. A hard disqualifier -> No-Bid.
(B) CONFIRMABLE INTERNAL GATES — company-side facts you CANNOT see from public data but the BD
    team knows or can arrange: holding a specific IDIQ/vehicle (or teaming with a holder); a
    facility/personnel clearance level; relevant past performance; DCAA accounting; ISO/CMMI.
    These are NOT reasons to No-Bid — they are GATES TO CONFIRM. If the opportunity otherwise
    FITS, recommend Bid (or Watch) and LIST these gates. Treating "I can't verify X from public
    data" as "X is absent" is the #1 mistake — do not make it.

DEFAULT POSTURE:
- Strong fit (NAICS/scope match, eligible or teamable, in {company}'s wheelhouse) with only
  CONFIRMABLE gates -> BID, and name the gates to confirm. (A task order under an IDIQ {company}
  may or may not hold is a confirmable gate, NOT an automatic No-Bid.)
- Genuinely early-stage (Sources Sought / RFI) or promising-but-materially-uncertain -> WATCH.
- A HARD DISQUALIFIER or clear poor fit -> NO-BID.

WINNABILITY — use the web search tool for the agency, likely incumbent, and recent similar
awards. You have NO internal past-performance data: if you can't establish winnability from
public data, say so and treat it as a CONFIRMABLE gate, NOT a No-Bid reason. A recompete against
an incumbent is a RISK to weigh (need a discriminator + strong transition plan), not a blocker.

GROUNDING (do not fabricate): never invent dates, contract numbers, dollar amounts, incumbents,
award histories, or {company} past performance. If a fact isn't in the opportunity/documents or a
web result, mark it "unverified" or list it as a gate to confirm — do NOT assert it, and do NOT
let its absence flip a well-fit opportunity to No-Bid. Cite a source URL for any external fact.
The COMPANY PROFILE is the only company background you may assert without a citation.

PRIORITY (0-100, rough bands): 80-100 strong fit + winnable + urgent/high value; 55-79 solid Bid
with some confirmable gates; 35-54 Watch / uncertain; 0-34 No-Bid or poor fit.

Use the reasoning tool to think before deciding.

OUTPUT:
- bid_decision: "Bid" | "No-Bid" | "Watch" (per the framework above).
- priority_score: 0-100 per the bands.
- recommended_stage: Bid -> "Qualify"; Watch -> "Discover"; No-Bid -> "No-Bid".
- call_action: ONLY when bid_decision == "Bid". contact = the opportunity POC (or the agency
  contracting office); talking_point = one line on why to reach out. null otherwise.
- rationale: 2-5 sentences — the fit read, the specific GATES TO CONFIRM (spell them out, e.g.
  "confirm RS3 IDIQ access and SECRET FCL"), the winnability read, and the recommendation. Mark
  unverified facts as "unverified". Cite URLs for external facts.

After researching and reasoning, your FINAL message must be ONLY this JSON object —
no prose, no markdown fences, no <reasoning> tags:
{{"bid_decision": "Bid" | "No-Bid" | "Watch", "priority_score": <integer 0-100>,
  "rationale": "<2-5 sentences incl. the gates to confirm>", "recommended_stage": "Qualify" | "Discover" | "No-Bid",
  "call_action": {{"contact": "<who>", "channel": "email", "talking_point": "<one line>"}}}}
Use null for "call_action" unless bid_decision is "Bid".
"""

_OPP_FIELDS = [
    "title", "solicitation_number", "notice_id", "agency", "naics", "psc_code",
    "set_aside", "opp_type", "posted_date", "response_deadline", "estimated_value",
    "place_of_performance", "poc_name", "poc_email", "description", "link",
]


def build_analyst_agent(organization_id: str | None = None) -> Agent:
    """Build a fresh Analyst Agent grounded in the org's own company profile (from its UEI)."""
    company, profile = company_context(organization_id or "")
    return Agent(
        name="Analyst",
        model=get_chat_llm_agno(model=settings.ANALYST_MODEL),
        tools=[create_exa_web_search_tool(), create_reasoning_tool()],
        instructions=_instructions(company, profile),
        debug_mode=True
    )


def _format_opportunity(opp: dict, today: str) -> str:
    lines = [f"Today's date: {today}", "", "OPPORTUNITY:"]
    for key in _OPP_FIELDS:
        val = opp.get(key)
        if val not in (None, ""):
            lines.append(f"- {key}: {val}")
    extra = opp.get("extra") or {}
    if extra:
        lines.append(f"- additional notes: {extra}")
    return "\n".join(lines) + document_context(opp)


def analyze_opportunity(
    opp: dict, today: str | None = None, max_attempts: int = 3
) -> AnalystVerdict:
    """Run the Analyst Agent on one opportunity and return its structured verdict.

    Reasoning models (Gemini) occasionally return empty/unparseable content — a
    flaky one-off. Retry the run a few times before giving up; a fresh run almost
    always produces clean JSON.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    organization_id = str(opp.get("organization_id") or "")
    message = _format_opportunity(opp, today)
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        result = build_analyst_agent(organization_id).run(message)
        try:
            return coerce_output(result.content, AnalystVerdict)
        except Exception as e:  # empty content / invalid JSON -> retry the run
            last_err = e
            logger.warning(
                "Analyst output unparseable (attempt %d/%d) for %s: %s",
                attempt, max_attempts, opp.get("id"), e,
            )
    raise last_err  # type: ignore[misc]
