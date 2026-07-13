"""Build an opportunity's `document_text` from many parsed files.

Strategy (chosen for govcon capture — a bid/no-bid decision that must not lose
requirements): STUFF if small, else summarize ONE DOCUMENT PER CALL and merge.

- SMALL: total parsed text <= DOC_DIGEST_STUFF_MAX_CHARS -> return it verbatim,
  no model call. This ceiling matches the Analyst's own document_context cap
  (utils.doc_parse.document_context default; both ~500k tokens), so small uploads
  reach the Analyst losslessly.
- LARGE: MAP each document (one call per document — natural boundaries, so a
  summary never mixes two documents) through DOC_DIGEST_MODEL into a faithful
  extraction-oriented digest (preserve solicitation #, agency, NAICS/PSC, set-aside,
  dates/deadline, scope/PWS requirements, evaluation criteria, deliverables, POC —
  no invention), then REDUCE the per-document digests into one coherent brief.

The digest model is used ONLY here; the Analyst keeps ANALYST_MODEL. Reuses the
cached OpenRouter ChatOpenAI factory (client.llm_client.get_chat_llm).
"""
from __future__ import annotations

import logging

from app.settings import settings
from client.llm_client import get_chat_llm

logger = logging.getLogger(__name__)

_MAP_PROMPT = (
    "You are digesting ONE document from a government solicitation package so a "
    "capture analyst can make a bid/no-bid decision. Summarize it FAITHFULLY and "
    "densely. Preserve every hard fact: solicitation/notice number, agency & office, "
    "NAICS/PSC codes, set-aside type, all dates and the response deadline, scope of "
    "work / PWS requirements, evaluation criteria (Sections L & M), required "
    "deliverables, submission instructions, and points of contact. Keep specifics "
    "(numbers, thresholds, clause references) verbatim. Do NOT invent anything and do "
    "NOT editorialize — if a field isn't present, omit it.\n\n"
    "DOCUMENT ({idx} of {total}):\n{doc}\n\nFAITHFUL DIGEST:"
)

_REDUCE_PROMPT = (
    "Below are faithful digests of the individual documents in ONE government "
    "solicitation package. Merge them into ONE coherent brief, ordered like a "
    "solicitation (overview & IDs -> scope/requirements -> evaluation criteria -> "
    "deliverables -> key dates/deadline -> submission instructions -> points of "
    "contact). Preserve every hard fact and specific figure; remove only duplication. "
    "Do NOT invent anything.\n\nDOCUMENT DIGESTS:\n{digests}\n\nMERGED SOLICITATION BRIEF:"
)


def _invoke(llm, prompt: str) -> str:
    """ChatOpenAI.invoke(str) -> AIMessage; return its text content."""
    resp = llm.invoke(prompt)
    content = getattr(resp, "content", resp)
    return (content or "").strip() if isinstance(content, str) else str(content)


def digest_documents(
    texts: list[str],
    *,
    model: str | None = None,
    stuff_max_chars: int | None = None,
) -> str:
    """Combine per-file parsed texts into one `document_text` for the Analyst.

    `texts` is one entry per uploaded document. Best-effort: a failed map call
    falls back to a truncated raw slice of that document so one bad LLM call never
    drops a whole document. Returns "" for no input.
    """
    model = model or settings.DOC_DIGEST_MODEL
    stuff_max = stuff_max_chars or settings.DOC_DIGEST_STUFF_MAX_CHARS

    parts = [t.strip() for t in (texts or []) if t and t.strip()]
    if not parts:
        return ""
    combined = "\n\n".join(parts)

    # SMALL — whole package fits; keep verbatim, no model call.
    if len(combined) <= stuff_max:
        logger.info("doc_digest: stuffing %d chars verbatim (<= %d)", len(combined), stuff_max)
        return combined

    # LARGE — one summarization call per document, then merge.
    llm = get_chat_llm(model=model)
    logger.info("doc_digest: summarizing %d documents (one call each) with %s", len(parts), model)

    digests: list[str] = []
    for i, doc in enumerate(parts, start=1):
        try:
            digest = _invoke(llm, _MAP_PROMPT.format(idx=i, total=len(parts), doc=doc))
            digests.append(digest or doc[: stuff_max // 3])
        except Exception as exc:  # noqa: BLE001 — never lose a document to one bad call
            logger.warning("doc_digest map failed on document %d/%d: %s", i, len(parts), exc)
            digests.append(doc[: stuff_max // 3] + "\n…[digest unavailable — raw excerpt]")

    # A single document needs no merge pass.
    if len(digests) == 1:
        return digests[0]

    joined = "\n\n".join(f"[Document {i}] {d}" for i, d in enumerate(digests, start=1))
    try:
        merged = _invoke(llm, _REDUCE_PROMPT.format(digests=joined))
    except Exception as exc:  # noqa: BLE001 — fall back to the concatenated digests
        logger.warning("doc_digest reduce failed: %s", exc)
        merged = ""
    return merged or joined
