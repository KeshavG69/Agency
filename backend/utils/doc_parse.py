"""Parse a solicitation document (PDF/Office/image) to text via LiteParse.

LiteParse is local + model-free (Rust core, PDFium + Tesseract OCR) — no API key,
nothing leaves the box. We render to Markdown so headings/tables/lists survive,
which the downstream agents read to ground their answers in the real document.

`parse_document` accepts a local path OR an http(s) URL (downloaded first).
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _parser():
    # Built once and reused; markdown output preserves document structure.
    from liteparse import LiteParse

    return LiteParse(output_format="markdown", quiet=True)


def _is_url(src: str) -> bool:
    return src.lower().startswith(("http://", "https://"))


_KNOWN_EXT = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".rtf",
              ".csv", ".htm", ".html", ".odt", ".ods", ".odp")


def _ext_of(name: str | None) -> str | None:
    m = re.search(r"(\.[A-Za-z0-9]{2,5})(?:[?#].*)?$", (name or "").strip())
    ext = (m.group(1) if m else "").lower()
    return ext if ext in _KNOWN_EXT else None


def _sniff_ext(data: bytes) -> str | None:
    """Identify a file from its first bytes. An Office Open XML file (.docx/.xlsx/.pptx) IS a zip
    archive, so "starts with PK" alone is ambiguous — look inside for which part it carries."""
    if data[:4] == b"%PDF":
        return ".pdf"
    if data[:2] == b"PK":
        import io
        import zipfile

        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except Exception:  # noqa: BLE001 — a real (non-Office) zip, or a damaged file
            return None
        for prefix, ext in (("word/", ".docx"), ("xl/", ".xlsx"), ("ppt/", ".pptx")):
            if any(n.startswith(prefix) for n in names):
                return ext
    return None


def _download_name(resp: "httpx.Response") -> str | None:
    """The filename the server says it is sending (Content-Disposition), if any."""
    cd = resp.headers.get("content-disposition") or ""
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
    return m.group(1) if m else None


def parse_document(
    src: str, *, max_chars: int | None = None, timeout: float = 60.0,
    filename: str | None = None,
) -> str | None:
    """Parse a document at `src` (URL or local path) -> Markdown text.

    Returns None on any failure (download or parse) so ingestion never breaks on
    one bad document. `max_chars` optionally truncates very large documents.

    WHY A DOWNLOAD IS WRITTEN TO A TEMP FILE. LiteParse accepts raw bytes for PDF ONLY (its own
    docstring: "Path to the document file, or raw PDF bytes"). Every other format needs a path
    whose extension names the format. This used to hand it the downloaded bytes directly, so
    every .docx / .xlsx / .pptx fetched by URL failed — sniffed as "unsupported file format:
    .zip", because an Office file is a zip inside. A live backfill lost twelve Performance Work
    Statements that way, and every Word/Excel file a rep uploaded by hand failed the same way.

    The extension comes from, in order: the `filename` the caller knows, the server's
    Content-Disposition header, the URL path, then the bytes themselves.
    """
    if not src or not src.strip():
        return None
    src = src.strip()
    tmp_path: str | None = None
    try:
        if _is_url(src):
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(src)
                resp.raise_for_status()
                data = resp.content
            ext = (_ext_of(filename) or _ext_of(_download_name(resp))
                   or _ext_of(str(resp.url).split("?")[0]) or _ext_of(src.split("?")[0])
                   or _sniff_ext(data))
            if ext in (None, ".pdf"):
                result = _parser().parse(data)  # PDF (or unknown): bytes are supported
            else:
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as fh:
                    fh.write(data)
                    tmp_path = fh.name
                result = _parser().parse(tmp_path)
        else:
            result = _parser().parse(src)  # local path
        text = (result.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — never let a bad doc break ingestion
        logger.warning("parse_document failed for %s: %s", (filename or src)[:120], exc)
        return None
    finally:
        if tmp_path:
            import os

            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if not text:
        return None
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n\n…[truncated]"
    return text


def document_context(opp: dict, max_chars: int | None = None) -> str:
    """A prompt block carrying the opportunity's parsed solicitation text.

    Returns "" when there's no document, so callers can append it unconditionally.
    Every agent appends this so its answers are grounded in the real solicitation.

    The cap defaults to DOC_DIGEST_STUFF_MAX_CHARS — the same ceiling the digest stores text
    under — so the two cannot drift. This used to be a hard-coded 2,000,000 (~500k tokens),
    left behind when the digest cap was cut to fit the self-hosted model's 64k context; once
    SAM.gov documents started arriving, that stale default would have put a prompt several
    times larger than the model's window on every agent call.
    """
    from app.settings import settings

    if max_chars is None:
        max_chars = settings.DOC_DIGEST_STUFF_MAX_CHARS
    text = (opp.get("document_text") or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n…[truncated]"
    return (
        "\n\nSOLICITATION DOCUMENT (full parsed text — ground your analysis in THIS, "
        "not assumptions):\n"
        "========================================\n"
        f"{text}\n"
        "========================================"
    )
