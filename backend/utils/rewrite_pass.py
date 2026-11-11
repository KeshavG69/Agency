"""Reword the proposal writer's prose with an open-weight model before it is rendered.

Why: the writer (WRITER_MODEL, Claude Haiku) watermarks its text. That mark isn't in the
characters (utils/scrub.py handles those) but in which words were picked, so the only way to clear it
is to have a model that adds no mark pick the words again. A watermark is added by whoever runs the
model, so the rewording runs on Gemma 4 with the hosts pinned (settings.REWRITE_*), or on the lab box.

What changes: only the writer's own prose (paragraphs, bullets, the narrative inside past
performance and resumes, assumptions). Titles, headings, tables, the solicitation's own words,
quotes and every code-filled fact stay exactly as they are.

What can't change: code compares every rewritten passage with the original and keeps the original
if a number, an identifier, an acronym or a [NEEDS: …]/[CHECK: …] marker moved, the length drifted,
a banned word crept in, an active sentence went passive ("…was provided by Nexagen") or a run-in
label ("Staffing: …") was lost. Grounding has already run, so this is the only check on the new wording.
A sentence grounding flagged is wrapped whole in [CHECK: …] and is therefore copied unchanged: a
person edits it anyway.

Did it actually change? A light edit leaves most of a watermark in place (OpenAI's own test: 10% of
words swapped only cut detection from ~92% to ~66%). So each passage's word-pair overlap with the
original is measured; one that comes back too close is asked for again, harder.

It never raises: a failure keeps the writer's wording and tells the reviewer, because a retry of the
whole task would repeat the expensive writing call.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import httpx

from app.settings import settings
from client.langfuse_client import observe

logger = logging.getLogger(__name__)

_MIN_WORDS = 6           # shorter than this carries no usable watermark signal
_BATCH_WORDS = 2500      # per call: well inside the model's output budget
_BATCH_ITEMS = 40
_MAX_SIMILARITY = 0.5    # word-pair overlap above this is a light edit, not a rewording
_LENGTH = (0.8, 1.25)    # a reworded passage stays about as long: the page limit was met already

_MARKER = re.compile(r"\[(?:NEEDS|CHECK):[^\]]*\]")
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9&]*[A-Z0-9]\b")
_IDENT = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"
                    r"|[\w.+-]+@[\w-]+\.[\w.]+"
                    r"|\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]{2,}(?:-[A-Z0-9]{1,}){1,4}\b"
                    r"|\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,}\b")
_STRENGTH = re.compile(r"\b(shall|must|may|might|could|should|guarantee[sd]?|possibly)\b")
# "Answer: Possibly" — a solicitation that asks for Yes / No / Possibly wants exactly that word.
_ANSWER = re.compile(r"\b(?:answer|response)\s*:\s*(yes|no|possibly)\b", re.I)
# A passive with its doer named ("...was provided by Nexagen"): the easy way for a model to make a
# sentence look different, and the stiffest way to say it. The writer almost never writes one.
_PASSIVE = re.compile(r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?"
                      r"\w+(?:ed|en|wn|nt|lt|ld|ne|pt|ught|id|un)\b(?:\s+\S+){0,6}?\s+by\b", re.I)
# "Staffing: ..." — a run-in label the reader scans for.
_LABEL = re.compile(r"^\s*([A-Z][^:.\n]{1,45}):\s")
# The writer's own banned list (agent/proposal_writer.py _instructions).
_BANNED = ("robust", "seamless", "leverage", "cutting-edge", "state-of-the-art", "best-in-class",
           "world-class", "holistic", "synergy", "unparalleled", "comprehensive solution",
           "we are excited")

_SYSTEM = """\
You reword passages from a government contract proposal. Write each passage again so that nearly
every sentence uses different words, while it says exactly the same thing. Rebuild each sentence:
choose different verbs, nouns and connecting words, and reorder its phrases, rather than swapping
a few words for synonyms.

Rules:
- Keep the voice. Whoever acts in the original still acts in yours, as the subject of an active
  verb: "We run the help desk" can become "Our team operates the help desk", never "The help desk
  is run by us" or "...by Nexagen". Don't turn an active sentence into a passive one.
- To change the wording without going passive: name the company or say "we" / "our team" (either
  is fine as the subject), pick a different verb and a different way to say the object, move a
  phrase about time or place to the other end of the sentence, and split a long sentence in two or
  join two short ones within the passage.
- A passage that starts with a label and a colon ("Staffing: ...", "Assumption 2: ...") keeps that
  label exactly as written; reword only what follows it.
- A one-word answer the solicitation asked for ("Answer: Yes", "No", "Possibly") stays that exact
  word: "Possibly" never becomes "Perhaps" or "Maybe".
- Keep every fact, claim and promise, and how strong it is. Use "shall", "must", "may", "should"
  and "could" exactly where the original does and nowhere else: never turn "will" into "shall" or
  "must". Add nothing. Drop nothing.
- Copy these exactly as written: every number, date, dollar amount and percentage; every acronym;
  every contract, document or task number; email addresses; names of people, companies, agencies,
  systems, products and places; and any text in square brackets, such as [NEEDS: ...] or
  [CHECK: ...], character for character.
- Keep the plain, formal tone of a seasoned proposal manager: short, specific, factual sentences.
- Keep each passage about as long as the original (within 10%).
- Never use these words: robust, seamless, leverage, cutting-edge, state-of-the-art, best-in-class,
  world-class, holistic, synergy, unparalleled, "comprehensive solution", "we are excited".
- One output per input, in the same order. Don't merge, split or skip passages.

Reply with ONLY a JSON object: {"passages": ["...", "..."]}"""

_HARDER = """

Your last attempt kept too much of the original wording, or turned a sentence passive. Keep no
run of more than three original words except the names, numbers and terms that must stay exact,
and keep every sentence active with the same subject as the original."""


# ---- which text is the writer's prose ---------------------------------------------------------

Slot = tuple[str, Callable[[str], None]]


def _slots(doc) -> list[Slot]:
    out: list[Slot] = []

    def add(text: str, put: Callable[[str], None]) -> None:
        bare = _MARKER.sub("", text or "")
        if len(bare.split()) >= _MIN_WORDS:
            out.append((text, put))

    def at(seq: list, i: int) -> Callable[[str], None]:
        return lambda v: seq.__setitem__(i, v)

    def attr(obj, name: str) -> Callable[[str], None]:
        return lambda v: setattr(obj, name, v)

    for s in doc.sections:
        for i, t in enumerate(s.paragraphs):
            add(t, at(s.paragraphs, i))
        for i, t in enumerate(s.bullets):
            add(t, at(s.bullets, i))
        for r in s.references:
            add(r.scope.text, attr(r.scope, "text"))
            for w in r.work:
                add(w.work.text, attr(w.work, "text"))
                add(w.relevance, attr(w, "relevance"))
            for c in r.results:
                add(c.text, attr(c, "text"))
        for p in s.people:
            for c in p.summary:
                add(c.text, attr(c, "text"))
            for q in p.requirements:
                add(q.experience.text, attr(q.experience, "text"))
    for i, a in enumerate(doc.assumptions):
        add(a, at(doc.assumptions, i))
    return out


# ---- the checks --------------------------------------------------------------------------------

def rejected(old: str, new: str) -> str | None:
    """Why `new` can't replace `old`, or None when it can."""
    if not isinstance(new, str) or not new.strip():
        return "empty"
    if any(x in new for x in ("[[", "]]", "§")):
        return "citation debris"
    n_old, n_new = len(old.split()), len(new.split())
    ratio = n_new / max(1, n_old)
    # A short line gets a few words of slack: on 8 words, 20% is one word.
    if not (_LENGTH[0] <= ratio <= _LENGTH[1] or abs(n_new - n_old) <= 3):
        return f"length x{ratio:.2f}"
    if Counter(_MARKER.findall(old)) != Counter(_MARKER.findall(new)):
        return "marker changed"
    rest_old, rest_new = _MARKER.sub("", old), _MARKER.sub("", new)
    if Counter(_NUMBER.findall(rest_old)) != Counter(_NUMBER.findall(rest_new)):
        return "number changed"
    if Counter(_IDENT.findall(rest_old)) != Counter(_IDENT.findall(rest_new)):
        return "identifier changed"
    if set(_ACRONYM.findall(rest_old)) != set(_ACRONYM.findall(rest_new)):
        return "acronym changed"
    low_old, low_new = old.lower(), new.lower()
    if any(b in low_new and b not in low_old for b in _BANNED):
        return "banned word"
    # "will" -> "shall" or "must" makes a promise stronger, "may" weaker: in a proposal that's a
    # different commitment. "will" itself comes and goes as sentences are rebuilt.
    if set(_STRENGTH.findall(low_old)) != set(_STRENGTH.findall(low_new)):
        return "commitment strength changed"
    if [a.lower() for a in _ANSWER.findall(old)] != [a.lower() for a in _ANSWER.findall(new)]:
        return "answer word changed"
    if len(_PASSIVE.findall(rest_new)) > len(_PASSIVE.findall(rest_old)):
        return "turned passive"
    label = _LABEL.match(old)
    if label and not new.lstrip().startswith(label.group(1) + ":"):
        return "label changed"
    return None


def similarity(a: str, b: str) -> float:
    """Share of word pairs the two have in common (Jaccard): 1.0 is the same wording."""
    def pairs(t: str) -> set[tuple[str, str]]:
        w = re.findall(r"[a-z0-9']+", _MARKER.sub("", t).lower())
        return set(zip(w, w[1:]))

    pa, pb = pairs(a), pairs(b)
    return len(pa & pb) / len(pa | pb) if pa | pb else 1.0


# ---- the model call ----------------------------------------------------------------------------

def _allowed() -> list[str]:
    return [p.strip().lower() for p in settings.REWRITE_PROVIDERS.split(",") if p.strip()]


def _parse(content: str, n: int) -> list[str] | None:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        got = json.loads(text[start:end + 1]).get("passages")
    except (ValueError, AttributeError):
        return None
    if not isinstance(got, list) or len(got) != n:
        return None
    return [g if isinstance(g, str) else "" for g in got]


@observe(name="rewrite-pass", as_type="generation")
def _call(passages: list[str], harder: bool) -> list[str] | None:
    """One batch reworded, or None. Raises on a transport error."""
    base = (settings.REWRITE_BASE_URL or settings.OPENROUTER_BASE_URL).rstrip("/")
    key = (settings.LLM_API_KEY or "local") if settings.REWRITE_BASE_URL else settings.OPENROUTER_API_KEY
    words = sum(len(p.split()) for p in passages)
    body: dict = {
        "model": settings.REWRITE_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM + (_HARDER if harder else "")},
            {"role": "user", "content": f"{len(passages)} passages:\n"
                                        + json.dumps({"passages": passages}, ensure_ascii=False)},
        ],
        # Picking the words again is the whole point, so sampling stays loose.
        "temperature": 0.8,
        "max_tokens": max(2000, int(words * 2.5)),
    }
    if settings.REWRITE_BASE_URL:  # the lab box (llama.cpp): no thinking, it only burns budget
        body["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        # `order` makes OpenRouter move down the list when a host is rate-limited (they 429 in
        # bursts); `only` + no fallbacks keeps it from ever leaving the list. Quantizations come
        # from settings ("unknown" is what Modal reports).
        quants = [q.strip() for q in settings.REWRITE_QUANTIZATIONS.split(",") if q.strip()]
        body["provider"] = {"order": _allowed(), "only": _allowed(), "allow_fallbacks": False,
                            "data_collection": "deny", "quantizations": quants}
        body["reasoning"] = {"enabled": False}
    resp = httpx.post(f"{base}/chat/completions", json=body, timeout=300,
                      headers={"Authorization": f"Bearer {key}"})
    resp.raise_for_status()
    data = resp.json()
    served = str(data.get("provider") or "").lower().replace(" ", "")
    if not settings.REWRITE_BASE_URL and served and served not in _allowed():
        # OpenRouter honours `only`; this is the belt to its braces. Text from a host we didn't
        # choose may carry that host's mark, so it is not used.
        logger.error("Rewrite pass: served by %r, not one of %s; discarded", served, _allowed())
        return None
    return _parse(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "",
                  len(passages))


def _attempt(passages: list[str], harder: bool) -> list[str] | None:
    for wait in (0, 5, 15, 30):
        if wait:
            time.sleep(wait)  # the pinned hosts rate-limit (429) in bursts
        try:
            got = _call(passages, harder)
            if got is not None:
                return got
        except Exception as exc:  # noqa: BLE001 — a failed batch keeps the writer's wording
            logger.warning("Rewrite pass: batch of %d failed: %s", len(passages), exc)
    return None


def _batches(indexes: list[int], slots: list[Slot]) -> list[list[int]]:
    out: list[list[int]] = [[]]
    words = 0
    for i in indexes:
        n = len(slots[i][0].split())
        if out[-1] and (words + n > _BATCH_WORDS or len(out[-1]) >= _BATCH_ITEMS):
            out.append([])
            words = 0
        out[-1].append(i)
        words += n
    return [b for b in out if b]


# ---- the entry point ---------------------------------------------------------------------------

def rewrite_document(doc) -> list[str]:
    """Reword `doc`'s prose in place. Returns lines for the reviewer (none when all went through)."""
    if not settings.REWRITE_MODEL:
        return []
    if not settings.REWRITE_BASE_URL and not settings.OPENROUTER_API_KEY:
        return ["The wording pass didn't run (no OpenRouter key), so this draft keeps the writer's own wording."]
    slots = _slots(doc)
    if not slots:
        return []

    best: dict[int, tuple[str, float]] = {}
    reasons: Counter = Counter()
    pending = list(range(len(slots)))
    # One host slot on the lab box; OpenRouter takes a few batches at once.
    workers = 1 if settings.REWRITE_BASE_URL else 4
    for harder in (False, True):
        batches = _batches(pending, slots)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outputs = list(pool.map(lambda b: _attempt([slots[i][0] for i in b], harder), batches))
        pending = []
        for batch, got in zip(batches, outputs):
            for j, i in enumerate(batch):
                new = got[j] if got else None
                why = "call failed" if new is None else rejected(slots[i][0], new)
                if why:
                    reasons[why] += 1
                    pending.append(i)
                    continue
                sim = similarity(slots[i][0], new)
                if i not in best or sim < best[i][1]:
                    best[i] = (new, sim)
                if sim > _MAX_SIMILARITY:
                    pending.append(i)  # usable, but ask once more for a real rewording
        if not pending:
            break

    for i, (new, _) in best.items():
        slots[i][1](new)
    kept = len(slots) - len(best)
    light = sum(1 for _, s in best.values() if s > _MAX_SIMILARITY)
    mean = sum(s for _, s in best.values()) / len(best) if best else 1.0
    logger.info("Rewrite pass: %d/%d passages reworded (mean overlap %.2f, %d light), kept %d; %s",
                len(best), len(slots), mean, light, kept, dict(reasons) or "no rejections")
    if kept:
        return [f"{kept} passage(s) kept the writer's own wording: the rewording pass failed or "
                f"would have changed a fact. Rewrite the document to try again."]
    return []
