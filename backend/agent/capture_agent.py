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
import logging
from functools import lru_cache
from pathlib import Path

from agno.agent import Agent
from agno.media import Image as AgnoImage
from agno.skills import LocalSkills, Skills
from agno.tools.function import ToolResult

from agent.company_profile import company_context
from app.settings import settings
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

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# Extension -> Agno/OpenAI image format. Only these are shown to the model as a vision block.
_IMAGE_FORMATS = {
    "png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif",
    "webp": "webp", "bmp": "bmp", "tif": "tiff", "tiff": "tiff",
}


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

3. Write EACH deliverable in the right tone (customer-facing for the external ones,
   internal/strategic for the capture plan), grounded in the strategy + real past performance.

USE RELEVANT VISUALS FROM SHAREPOINT — a strong deliverable often carries a real graphic (the
company logo, a capability or architecture diagram, a past-performance photo, an org chart, a
certification/accreditation badge). For each deliverable:
   - Search SharePoint with `search_sharepoint` for candidate images (e.g. "logo", "architecture
     diagram", "capability graphic", "certification badge"). Image files come back with an `id`
     and a `drive_id`.
   - Call `fetch_sharepoint_image(drive_id, item_id, filename)` — it SHOWS you the image and
     stages it in the workspace. LOOK at the image: embed it ONLY if it genuinely strengthens the
     document. Never embed decorative filler, never an image you didn't fetch and actually see.
   - Embed accepted images in python_repl_tool via the right skill (docx `add_picture`, pptx
     `add_picture`), sized and placed sensibly, with a short caption where it helps.
   - If SharePoint has NO suitable image and the document would benefit from an illustrative or
     conceptual visual (a solution-concept diagram, a process/architecture concept, a cover
     graphic), you may GENERATE one with `generate_image(description, filename)` — it shows you the
     result to review, then embed it the same way. NEVER generate anything meant to look like
     factual evidence (past-performance photos, screenshots, real logos, charts of invented data);
     real proof must come from SharePoint.

MANDATORY PER-DELIVERABLE PROCEDURE — you MUST complete ALL of these steps, in order, for
every single deliverable. There are NO exceptions and NO shortcuts:
   (a) GENERATE the file in `python_repl_tool` with the right skill (pptx for a briefing, docx
       otherwise). Use a simple filename with NO path (e.g. "capability_briefing.pptx").
   (b) UPLOAD it by CALLING the `s3_upload_tool(filename="<that exact filename>")` tool. This is
       a REAL tool call you must actually make — not something you describe or write in prose.
   (c) CHECK the tool's result. It must return `success: true`. If it returns `success: false`
       (e.g. "file not found"), the file name is wrong or the file wasn't written — fix it in
       `python_repl_tool` and CALL `s3_upload_tool` AGAIN. Repeat until you get `success: true`.
   (d) Only a deliverable that reached `success: true` may appear in your final JSON.

ABSOLUTE RULES:
   - The download link is taken ONLY from the actual `s3_upload_tool` result — NEVER from
     anything you type. Do NOT invent, guess, copy, or write any URL (no "https://example.com",
     no placeholder, no made-up link). There is no `doc_url` field for you to fill.
   - You may NOT list a deliverable you did not successfully upload with `s3_upload_tool`. A
     deliverable without a matching successful upload is DISCARDED and the work is wasted.
   - If you find yourself about to write the final JSON without having called `s3_upload_tool`
     for a document, STOP and call the tool first.

BEFORE you emit the final JSON, self-check: for every deliverable you are about to list, did you
actually call `s3_upload_tool` and see `success: true`? If not, go back and upload it now.

Return `deliverables` — a list, one entry per document you generated AND successfully uploaded,
each with doc_type, title, the EXACT `filename` you passed to `s3_upload_tool`, and a 2-3
sentence summary.

Ground everything in your research, our SharePoint past performance, and the opportunity data.
Be honest about unknowns — never fabricate facts about the customer, competitors, or our past
performance.

{sharepoint_tool_instructions()}

After generating + uploading the document(s), your FINAL message must be ONLY this JSON —
no prose, no markdown fences, no <reasoning> tags:
{{"deliverables": [{{"doc_type": "rfi_response" | "white_paper" | "capability_briefing" | "capture_plan", "title": "<title>", "filename": "<exact filename you uploaded>", "summary": "<2-3 sentences>"}}]}}
"""


def build_capture_agent(
    organization_id: str | None = None, employee_email: str | None = None,
    opp: dict | None = None, real_uploads: dict | None = None,
) -> Agent:
    """Build the Capture agent — grounded in the org's company profile (from its UEI) AND its
    SharePoint past-performance material (RBAC-filtered to the acting employee).

    Every real s3_upload_tool upload is recorded into `real_uploads`, keyed by filename:
    {filename: {url, object_key, sharepoint_url, sharepoint_item_id}}. This is the AUTHORITATIVE
    source of a deliverable's download link — the caller resolves it by filename instead of
    trusting a URL the model self-reports (which it may fabricate for a file it never uploaded).
    When `opp` is a Bid with a provisioned SharePoint folder, the same bytes are ALSO filed into
    its 'Capture Docs' folder in the same pass (no download-then-reupload)."""
    company, profile = company_context(organization_id or "")

    def search_sharepoint_tool(query: str) -> str:
        """Search the company's SharePoint for past-performance / capability material relevant
        to `query` (e.g. "cybersecurity past performance", "SATCOM capability statement").
        Returns a JSON list of relevant docs/folders with paths + links — only documents the
        acting employee may read. Returns [] if nothing relevant."""
        return search_sharepoint(
            query, employee_email=employee_email, organization_id=organization_id or ""
        )

    def fetch_sharepoint_image(drive_id: str, item_id: str, filename: str) -> ToolResult:
        """Download an IMAGE from SharePoint BY ID, SHOW it to you visually for review, and stage
        it in the document workspace so you can EMBED it with python_repl_tool (docx `add_picture`,
        pptx `add_picture`).

        HOW: first find the image with `search_sharepoint` — it returns each file's `id` and
        `drive_id`. Pass those here plus a simple local `filename` KEEPING the real extension
        (e.g. "capability_diagram.png"). The image is returned to you as a picture you can SEE —
        look at it and embed it ONLY if it's genuinely relevant. After success, use `filename` as
        the LOCAL path inside python_repl_tool."""
        import os

        from utils.python_repl_tool import ensure_session_dir
        from utils.sharepoint_writer import download_drive_item

        try:
            content = download_drive_item(str(organization_id or ""), drive_id, item_id)
        except Exception as exc:  # noqa: BLE001 — report to the model, never crash the run
            return ToolResult(content=f"Could not fetch SharePoint file {item_id}: {exc}")

        safe = os.path.basename((filename or "image").strip()) or "image"
        with open(os.path.join(ensure_session_dir(), safe), "wb") as fh:
            fh.write(content)

        note = (f"Fetched '{safe}' ({len(content):,} bytes) into the document workspace. To embed "
                f"it, use the LOCAL filename '{safe}' in python_repl_tool (docx add_picture / pptx "
                f"add_picture).")
        fmt = _IMAGE_FORMATS.get(os.path.splitext(safe)[1].lower().lstrip("."))
        if not fmt:
            # Not a viewable image (e.g. a docx/pdf) — staged, but nothing to show visually.
            return ToolResult(content=note + " (Not a viewable image format — no visual preview.)")
        # The image ride-alongs as a vision block: Agno appends it as a follow-up user message so
        # the (vision-capable) model actually SEES the picture and can judge relevance/placement.
        return ToolResult(
            content=note + " The image is shown below — LOOK at it and embed it only if it "
                           "genuinely strengthens this deliverable (no decorative filler).",
            images=[AgnoImage(content=content, format=fmt)],
        )

    def generate_image(description: str, filename: str) -> ToolResult:
        """Generate an ORIGINAL image from a text `description` (GPT-image via OpenRouter), SHOW it
        to you, and stage it in the document workspace so you can EMBED it with python_repl_tool
        (docx `add_picture` / pptx `add_picture`).

        USE THIS for illustrative / conceptual visuals a document benefits from — a solution-concept
        diagram, a process/architecture concept, an abstract section or cover graphic — when there
        is no suitable REAL image in SharePoint.

        DO NOT use it to fabricate anything that would be read as factual evidence: no fake
        past-performance photos, no invented screenshots, no real company/agency logos, no charts of
        made-up data. Real capability proof must come from SharePoint (search_sharepoint /
        fetch_sharepoint_image).

        `description`: a specific prompt for the image. `filename`: a simple local name ending in
        .png (e.g. "solution_concept.png"). The image is returned to you visually — LOOK at it and
        embed it only if it's good."""
        import os

        from utils.image_gen import generate_image as _gen
        from utils.python_repl_tool import ensure_session_dir

        try:
            content, ext = _gen(description)
        except Exception as exc:  # noqa: BLE001 — report to the model, never crash the run
            return ToolResult(content=f"Image generation failed: {exc}")

        base = os.path.basename((filename or "generated").strip()) or "generated"
        if not os.path.splitext(base)[1]:
            base = f"{base}.{ext}"
        with open(os.path.join(ensure_session_dir(), base), "wb") as fh:
            fh.write(content)
        fmt = _IMAGE_FORMATS.get(os.path.splitext(base)[1].lower().lstrip("."), "png")
        return ToolResult(
            content=(f"Generated '{base}' ({len(content):,} bytes) from your description and staged "
                     f"it in the workspace. Embed it with the LOCAL filename '{base}' in "
                     f"python_repl_tool. The image is shown below — LOOK at it and embed it only if "
                     f"it's good and relevant."),
            images=[AgnoImage(content=content, format=fmt)],
        )

    def _record_upload(url: str, object_key: str, local_path: str, filename: str) -> None:
        """Post-upload hook: record the REAL upload (iDrive url + object key) so the deliverable's
        link comes from the tool, not the model. Then best-effort file the same bytes into the
        opp's 'Capture Docs' folder. iDrive is recorded FIRST so a SharePoint hiccup never loses
        the durable link."""
        if real_uploads is None:
            return
        real_uploads[filename] = {
            "url": url, "object_key": object_key,
            "sharepoint_url": None, "sharepoint_item_id": None,
        }
        if opp:
            try:
                with open(local_path, "rb") as fh:
                    content = fh.read()
                sp = file_to_capture_docs(str(organization_id or ""), opp, filename, content)
                if sp:
                    real_uploads[filename]["sharepoint_url"] = sp.get("sharepoint_url")
                    real_uploads[filename]["sharepoint_item_id"] = sp.get("sharepoint_item_id")
            except Exception as exc:  # noqa: BLE001 — iDrive is the durable copy; SP filing is a bonus
                logger.warning("Capture Docs filing failed for %s: %s", filename, exc)

    upload_tool = build_s3_upload_tool(on_uploaded=_record_upload if real_uploads is not None else None)

    return Agent(
        name="Capture",
        # Big output budget: the agent writes whole docx/pptx-generation scripts as a single
        # tool argument; 10k truncates the code mid-call -> "missing argument".
        model=get_chat_llm_agno(model=settings.CAPTURE_MODEL, max_tokens=60000),
        tools=[
            create_exa_web_search_tool(),
            # create_reasoning_tool(),
            search_sharepoint_tool,
            fetch_sharepoint_image,
            generate_image,
            *load_sharepoint_tools(sharepoint_entity(organization_id or "")),
            python_repl_tool,
            upload_tool,
        ],
        skills=get_capture_skills(),
        instructions=_instructions(company, profile),
        debug_mode=True,
    )


def generate_capture(opp: dict, employee_email: str | None = None) -> tuple[CaptureOutput, dict]:
    """Run the Capture agent on one opportunity → (strategy + deliverable(s), real_uploads).

    `real_uploads` maps each uploaded filename → {url, object_key, sharepoint_url,
    sharepoint_item_id}. The caller resolves each deliverable's real download link from here (by
    filename), so a deliverable the model listed but never actually uploaded has no entry and is
    dropped instead of persisted with a fabricated URL."""
    set_session_id(f"capture-{opp.get('id', 'default')}")
    real_uploads: dict = {}
    agent = build_capture_agent(
        str(opp.get("organization_id") or ""), employee_email, opp=opp, real_uploads=real_uploads
    )
    _skip = {"extra", "document_text"}  # document_text is appended cleanly below
    lines = [f"- {k}: {v}" for k, v in opp.items() if v not in (None, "", {}) and k not in _skip]
    message = "OPPORTUNITY:\n" + "\n".join(lines) + document_context(opp)
    # arun(): the doc-gen tools (python_repl_tool, upload tool) are async.
    result = asyncio.run(agent.arun(message))
    return coerce_output(result.content, CaptureOutput), real_uploads
