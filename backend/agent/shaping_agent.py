"""The Shaping Agent.

Phase 2 — produces the EXTERNAL, customer-facing deliverables that shape an
opportunity: RFI responses, white papers, capability briefings. It builds these
FROM the Capture Plan Agent's output (the internal strategy), generates the
document (docx for papers/RFIs, pptx for briefings), uploads it to iDrive, and
returns the URL.
"""
from __future__ import annotations

from agent.analyst_agent import NEXAGEN_PROFILE
from agent.capture_plan_agent import get_capture_skills
from agno.agent import Agent

from client.llm_client import get_chat_llm_agno
from models.capture import ShapingOutput
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.python_repl_tool import python_repl_tool, set_session_id
from utils.s3_upload_tool import s3_upload_tool
from utils.structured import coerce_output

_INSTRUCTIONS = f"""\
You are a proposal/capture writer for Nexagen Networks (a government contractor).
You are given an opportunity and the internal CAPTURE PLAN. YOU decide which
EXTERNAL, customer-facing deliverable(s) this opportunity actually needs, and
produce them.

NEXAGEN PROFILE:
{NEXAGEN_PROFILE}

Deliverable types you can produce (pick what fits THIS opportunity — usually one,
at most two; don't produce ones that aren't warranted):
- "rfi_response"        -> a formal response to the agency's Request for Information (Word / docx).
                           Choose this when the opportunity is a Sources Sought / RFI.
- "white_paper"        -> a technical capability white paper (Word / docx).
- "capability_briefing" -> a slide deck (PowerPoint / pptx).

For EACH deliverable you decide to produce:
1. Use the capture plan's win themes and discriminators as the backbone. Optionally
   web-search for supporting facts. Use the reasoning tool to plan the structure.
2. Write it in a professional, customer-facing tone, tailored to THIS opportunity/agency.
3. Generate the document with the right skill + python_repl_tool — pptx for a capability
   briefing, docx for an RFI response or white paper. Use a simple filename (no path).
4. Upload it with s3_upload_tool(filename="...") and take the returned url.

Return `deliverables` — a list, one entry per document produced, each with doc_type,
title, doc_url, and a 2-3 sentence summary.

Ground everything in the capture plan and the opportunity. Never fabricate facts or figures.

Your FINAL message must be ONLY this JSON — no prose, no markdown fences, no <reasoning> tags:
{{"deliverables": [{{"doc_type": "rfi_response" | "white_paper" | "capability_briefing", "title": "<title>", "doc_url": "<url>", "summary": "<2-3 sentences>"}}]}}
"""


def build_shaping_agent() -> Agent:
    """Build the Shaping Agent (cached model + web search + reasoning + doc-gen)."""
    return Agent(
        name="Shaping",
        model=get_chat_llm_agno(),
        tools=[
            create_exa_web_search_tool(),
            create_reasoning_tool(),
            python_repl_tool,
            s3_upload_tool,
        ],
        skills=get_capture_skills(),
        instructions=_INSTRUCTIONS,
    )


def generate_shaping_docs(opp: dict, capture_plan: str) -> ShapingOutput:
    """Agent decides which external deliverable(s) the opportunity needs and produces them."""
    set_session_id(f"shaping-{opp.get('id', 'default')}")
    agent = build_shaping_agent()
    opp_lines = [f"- {k}: {v}" for k, v in opp.items() if v not in (None, "", {}) and k != "extra"]
    message = (
        "OPPORTUNITY:\n" + "\n".join(opp_lines) + "\n\n"
        "CAPTURE PLAN (internal strategy to draw from):\n" + capture_plan
    )
    return coerce_output(agent.run(message).content, ShapingOutput)
