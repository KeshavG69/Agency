"""Gather a solicitation's ORIGINAL files as text, for work that can't use the digest.

`opportunity.document_text` is built for the Analyst: a long package is summarised to fit a
context window, which keeps the gist and loses the specifics. Compliance work needs those
specifics (a page limit, a font, a form number), so this re-reads the source: the uploaded files
for a manual opportunity, the live SAM.gov attachments for a notice. The digest is only the last
resort, and the result says so (`from_summary`) so the UI can warn the reader.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.settings import settings
from client.crm_store import get_crm_store
from client.idrive_storage import get_idrive_storage
from utils.doc_parse import parse_document
from utils.sam_documents import list_attachments, select_attachments

logger = logging.getLogger(__name__)

# Language that marks submission instructions and scoring. Counted, not just matched: nearly every
# file in a package says "solicitation" somewhere, so a yes/no test put an acronym list ahead of
# the RFP itself.
_INSTRUCTION = re.compile(
    r"instructions?,? (conditions|to offerors|for (the )?(preparation|submission))|"
    r"(proposal|quot\w+) (preparation |submission )?instructions|submission instructions|"
    r"evaluation (factors|criteria)|section [lm]\b|\b[lm]\.\d+\b|52\.212-[12]|page limit|"
    r"\bvolume (i{1,3}|iv|[1-4])\b|offerors? (shall|must) submit|basis (for|of) award",
    re.I)
# Kept around each instruction hit when a file is too big to read whole. Federal RFPs put
# Sections L and M near the END, so simply keeping the opening pages loses exactly these.
_WINDOW = 5000


def _score(name: str, text: str) -> int:
    """How much instruction language a file holds. The name counts: it's a deliberate signal."""
    return len(_INSTRUCTION.findall(text)) + 50 * len(_INSTRUCTION.findall(name.replace("_", " ")))


def _focus(text: str, room: int) -> str:
    """Fit a long file into `room` characters, keeping the opening (cover, dates, amendments)
    and the passages around its instructions, in document order."""
    if len(text) <= room:
        return text
    spans = [(0, min(6000, room))]
    for m in _INSTRUCTION.finditer(text):
        lo, hi = max(0, m.start() - _WINDOW // 5), m.start() + _WINDOW
        if lo <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    out, used = [], 0
    for lo, hi in spans:
        piece = text[lo:hi][: room - used]
        if not piece:
            break
        out.append(piece)
        used += len(piece)
    return "\n\n[…]\n\n".join(out)


@dataclass
class SolicitationFiles:
    text: str = ""
    read: list[str] = field(default_factory=list)      # files whose text was read
    not_read: list[str] = field(default_factory=list)  # attachments left out, or unreadable
    from_summary: bool = False                         # only the stored digest was available
    truncated: bool = False                            # the package was over the size cap


def _manual_files(opp: dict) -> list[tuple[str, str]]:
    crm, storage = get_crm_store(), get_idrive_storage()
    out = []
    for d in crm.list_documents(opp["id"]):
        if d.get("agent_id") != "manual_upload":
            continue
        url = storage.get_presigned_url(d["object_key"], expiration=3600) if d.get("object_key") else d.get("url")
        if not url:
            continue
        name = d.get("title") or "document"
        out.append((name, parse_document(url, filename=name, timeout=120.0) or ""))
    return out


def _sam_files(notice_id: str, result: SolicitationFiles) -> list[tuple[str, str]]:
    raw = list_attachments(notice_id)
    if not raw:
        return []
    out = []
    for a in select_attachments(raw):
        if not a.selected:
            result.not_read.append(a.name)  # named for the model: a required blank form lives here
            continue
        out.append((a.name, parse_document(a.download_url, timeout=120.0, filename=a.name) or ""))
    return out


def gather_files(opp: dict) -> SolicitationFiles:
    """Read one opportunity's original files. `opp` needs `id`, and `notice_id` if from SAM.gov."""
    result = SolicitationFiles()
    if opp.get("source") == "manual" or not opp.get("notice_id"):
        files = _manual_files(opp)
    else:
        files = _sam_files(opp["notice_id"], result)

    readable = []
    for name, text in files:
        if text.strip():
            readable.append((name, text))
        else:
            result.not_read.append(f"{name} (could not be read)")

    if not readable:
        result.from_summary = True
        result.text = opp.get("document_text") or opp.get("description") or ""
        return result

    readable.sort(key=lambda f: _score(f[0], f[1]), reverse=True)
    used, parts = 0, []
    for name, text in readable:
        room = settings.REQUIREMENTS_READ_CHARS - used
        if room < 2000:
            result.truncated = True
            result.not_read.append(f"{name} (package too large)")
            continue
        if len(text) > room:
            text, result.truncated = _focus(text, room), True
        parts.append(f"===== FILE: {name} =====\n{text}")
        result.read.append(name)
        used += len(text)
    if result.not_read:
        parts.append("===== OTHER FILES IN THE PACKAGE (not read) =====\n" + "\n".join(result.not_read))
    result.text = "\n\n".join(parts)
    return result
