"""The Capture agent (Phase 2) — merged capture-plan + shaping.

One agent, one pass: it works out the INTERNAL capture strategy (customer pain points,
mission objectives, competitor/incumbent read, win themes, discriminators, teaming) and
produces the EXTERNAL, customer-facing deliverable(s) the opportunity needs — grounded in
that strategy and the company's real past performance. The internal capture-plan document
is optional (only when it adds value). This replaces the separate Capture Plan + Shaping
agents, which shared every tool and ran back-to-back.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from agno.agent import Agent
from agno.skills import LocalSkills, Skills

from agent.company_profile import company_context
from client.llm_client import get_chat_llm_agno
from client.sharepoint_graph import search_sharepoint
from models.capture import CaptureOutput
from utils.agno_tools import create_exa_web_search_tool, create_reasoning_tool
from utils.composio_utils import sharepoint_entity
from utils.doc_parse import document_context
from utils.python_repl_tool import python_repl_tool, set_session_id
from utils.s3_upload_tool import build_s3_upload_tool
from utils.sharepoint_tools import load_sharepoint_tools, sharepoint_tool_instructions
from utils.sharepoint_writer import file_to_capture_docs
from utils.structured import coerce_output

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@lru_cache(maxsize=1)
def get_capture_skills() -> Skills:
    """Load the pdf/docx/pptx skills once per process."""
    return Skills(loaders=[LocalSkills(str(_SKILLS_DIR), validate=True)])


def _instructions(company: str, profile: str) -> str:
    return f"""\
You are a capture strategist AND proposal writer for {company} (a government contractor).
You are given ONE opportunity. Do BOTH jobs in a single pass:
  1) Work out the INTERNAL capture strategy — customer pain points, mission objectives,
     likely incumbent + competitor assessment, win themes, discriminators, teaming strategy.
  2) Produce the EXTERNAL, customer-facing deliverable(s) this opportunity actually needs,
     grounded in that strategy.

COMPANY PROFILE:
{profile}

GROUND IN OUR PAST CAPABILITY — use the `search_sharepoint` tool to pull the company's REAL
past-performance / capability material (capability statements, prior proposals, project
summaries), and the SharePoint read tools to open a specific document. Back every capability
claim with real past performance you find — never invent it.

Steps:
1. Research the customer/agency, likely incumbent, and competitors with the web search tool,
   AND search SharePoint for our relevant past performance. Use the reasoning tool to think
   through the capture strategy before writing anything.
2. Decide which deliverable(s) to produce (usually ONE, at most two — don't produce ones that
   aren't warranted):
   - "rfi_response"        -> a formal response to a Sources Sought / RFI (Word / docx).
   - "white_paper"        -> a technical capability white paper (Word / docx). SEE THE
                              WHITE-PAPER METHOD below — this one requires extra web research.
   - "capability_briefing" -> a capability slide deck (PowerPoint / pptx).
   - "capture_plan"       -> an OPTIONAL internal capture-plan document (Word / docx) for the
                              BD team — produce this ONLY when it genuinely adds value
                              (e.g. a complex, high-value pursuit worth a gate-review doc).

WHITE-PAPER METHOD (when you produce a "white_paper"):
   a. First, pin down the customer's actual technical PROBLEM / mission gap from the
      opportunity (and the incumbent's current approach, if any).
   b. Then USE THE WEB SEARCH TOOL to find the BEST, most ADVANCED way to solve it — current
      state-of-the-art methods, modern architectures/technologies, relevant standards or
      frameworks, and how leaders solve this class of problem TODAY (not the dated/default
      approach). Search a few angles; cite the sources you actually read.
   c. Pick the approach that solves the problem MEANINGFULLY BETTER than the status quo /
      incumbent, and explain WHY it's superior (performance, cost, risk, security, schedule).
   d. Write the white paper around that differentiated technical solution: problem statement →
      proposed advanced approach (with the cited rationale) → why it beats the current way →
      how {company} delivers it, backed by REAL past performance from SharePoint. Be technically
      specific and credible — a discriminator, not a generic capabilities brochure.
   Cite source URLs inline for the external tech claims; ground capability claims in SharePoint.

3. For EACH deliverable you produce: write it in the right tone (customer-facing for the
   external ones, internal/strategic for the capture plan), grounded in the strategy + real
   past performance. Generate it with the right skill + python_repl_tool (pptx for a briefing,
   docx otherwise; simple filename, no path), then upload with s3_upload_tool(filename="...")
   and take the returned url.

Return `deliverables` — a list, one entry per document produced, each with doc_type, title,
doc_url, and a 2-3 sentence summary.

Ground everything in your research, our SharePoint past performance, and the opportunity data.
Be honest about unknowns — never fabricate facts about the customer, competitors, or our past
performance.

{sharepoint_tool_instructions()}

After generating + uploading the document(s), your FINAL message must be ONLY this JSON —
no prose, no markdown fences, no <reasoning> tags:
{{"deliverables": [{{"doc_type": "rfi_response" | "white_paper" | "capability_briefing" | "capture_plan", "title": "<title>", "doc_url": "<url>", "summary": "<2-3 sentences>"}}]}}
"""


def deliverable_url_key(url: str) -> str:
    """A stable join key for a deliverable's iDrive URL — the DECODED path only, without the
    volatile presigned query string (X-Amz-Signature / X-Amz-Date / X-Amz-Expires …). The path
    carries the unique per-upload artifact id + filename, so it uniquely identifies the file; the
    signed query is what a model is most likely to reorder or re-encode when echoing the URL. Used
    on both sides of the sp_uploads join so the SharePoint copy links to its deliverable robustly."""
    return unquote(urlsplit(url or "").path)


def build_capture_agent(
    organization_id: str | None = None, employee_email: str | None = None,
    opp: dict | None = None, sp_uploads: dict | None = None,
) -> Agent:
    """Build the Capture agent — grounded in the org's company profile (from its UEI) AND its
    SharePoint past-performance material (RBAC-filtered to the acting employee).

    When `opp` (a Bid with a provisioned SharePoint folder) and `sp_uploads` are given, the
    upload tool ALSO files each generated deliverable into the opp's 'Capture Docs' folder in
    the SAME pass — no download-then-reupload — and records {idrive_url: {sharepoint_url,
    sharepoint_item_id}} into `sp_uploads` for the caller to persist onto the CRM document."""
    company, profile = company_context(organization_id or "")

    def search_sharepoint_tool(query: str) -> str:
        """Search the company's SharePoint for past-performance / capability material relevant
        to `query` (e.g. "cybersecurity past performance", "SATCOM capability statement").
        Returns a JSON list of relevant docs/folders with paths + links — only documents the
        acting employee may read. Returns [] if nothing relevant."""
        return search_sharepoint(
            query, employee_email=employee_email, organization_id=organization_id or ""
        )

    def _also_file_to_sharepoint(url: str, local_path: str, filename: str) -> None:
        """Post-upload hook: put the same local bytes into the opp's 'Capture Docs' subfolder.
        Best-effort — the upload tool swallows any raise, so iDrive stays the durable copy."""
        with open(local_path, "rb") as fh:
            content = fh.read()
        sp = file_to_capture_docs(str(organization_id or ""), opp or {}, filename, content)
        if sp and sp_uploads is not None:
            # Key on the STABLE url path (not the volatile signed query string) so capture_task
            # can still link this SharePoint copy to the deliverable even if the model re-encodes
            # the presigned URL when echoing it into its JSON.
            sp_uploads[deliverable_url_key(url)] = sp

    upload_tool = build_s3_upload_tool(
        on_uploaded=_also_file_to_sharepoint if (opp and sp_uploads is not None) else None
    )

    return Agent(
        name="Capture",
        # Big output budget: the agent writes whole docx/pptx-generation scripts as a single
        # tool argument; 10k truncates the code mid-call -> "missing argument".
        model=get_chat_llm_agno(max_tokens=60000),
        tools=[
            create_exa_web_search_tool(),
            create_reasoning_tool(),
            search_sharepoint_tool,
            *load_sharepoint_tools(sharepoint_entity(organization_id or "")),
            python_repl_tool,
            upload_tool,
        ],
        skills=get_capture_skills(),
        instructions=_instructions(company, profile),
        debug_mode=True,
    )


def generate_capture(opp: dict, employee_email: str | None = None) -> tuple[CaptureOutput, dict]:
    """Run the Capture agent on one opportunity → (strategy + deliverable(s), sp_uploads).

    `sp_uploads` maps each deliverable's iDrive url → {sharepoint_url, sharepoint_item_id} for the
    copies the upload tool filed into 'Capture Docs' (empty if SharePoint isn't set up)."""
    set_session_id(f"capture-{opp.get('id', 'default')}")
    sp_uploads: dict = {}
    agent = build_capture_agent(
        str(opp.get("organization_id") or ""), employee_email, opp=opp, sp_uploads=sp_uploads
    )
    _skip = {"extra", "document_text"}  # document_text is appended cleanly below
    lines = [f"- {k}: {v}" for k, v in opp.items() if v not in (None, "", {}) and k not in _skip]
    message = "OPPORTUNITY:\n" + "\n".join(lines) + document_context(opp)
    # arun(): the doc-gen tools (python_repl_tool, upload tool) are async.
    result = asyncio.run(agent.arun(message))
    return coerce_output(result.content, CaptureOutput), sp_uploads
