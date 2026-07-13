"""Parse a solicitation document (PDF/Office/image) to text via LiteParse.

LiteParse is local + model-free (Rust core, PDFium + Tesseract OCR) — no API key,
nothing leaves the box. We render to Markdown so headings/tables/lists survive,
which the downstream agents read to ground their answers in the real document.

`parse_document` accepts a local path OR an http(s) URL (downloaded first).
"""
from __future__ import annotations

import logging
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


def parse_document(src: str, *, max_chars: int | None = None, timeout: float = 60.0) -> str | None:
    """Parse a document at `src` (URL or local path) -> Markdown text.

    Returns None on any failure (download or parse) so ingestion never breaks on
    one bad document. `max_chars` optionally truncates very large documents.
    """
    if not src or not src.strip():
        return None
    src = src.strip()
    try:
        if _is_url(src):
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(src)
                resp.raise_for_status()
                data = resp.content  # bytes -> LiteParse.parse accepts bytes
            result = _parser().parse(data)
        else:
            result = _parser().parse(src)  # local path
        text = (result.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — never let a bad doc break ingestion
        logger.warning("parse_document failed for %s: %s", src[:120], exc)
        return None

    if not text:
        return None
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "\n\n…[truncated]"
    return text


def document_context(opp: dict, max_chars: int = 2000000) -> str:  # ~500k tokens @ ~4 chars/token
    """A prompt block carrying the opportunity's parsed solicitation text.

    Returns "" when there's no document, so callers can append it unconditionally.
    Every agent appends this so its answers are grounded in the real solicitation.
    """
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
