"""Email signature blocks — free job titles, seniority and phone numbers.

The signature at the bottom of a business email is the best source there is for a job
title: people update it the week they are promoted, which is more than can be said for
any profile or paid data vendor. It costs nothing — the message is already downloaded.

This replaces the per-person half of the paid enrichment we are retiring. Pure text
work: no AI, no network, no database.

    "Jane Reyes / VP, Business Development / Nexagen / (703) 555-0198"
        -> title="VP, Business Development"  seniority="VP"  function="BD & Capture"

WHY THE ORDER OF OPERATIONS MATTERS
The signature is at the BOTTOM of a message — but in a reply chain the bottom is the
OLDEST message, which carries somebody else's signature. So quoted history is stripped
FIRST; taking the tail of a raw reply chain would confidently file a stranger's job
title against this contact. That is exactly the failure the evidence model exists to
prevent, so the parser fails closed instead: it only returns a title that matched a
known role keyword, and returns None rather than guessing.

See docs/enrichment-implementation-plan.md §5.2. Emitted as evidence kind
`outlook.signature-block` (weight 0.80, primary) — see models/evidence.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional, Sequence

# How many trailing lines of the (de-quoted) message can plausibly be a signature.
_TAIL_LINES = 14

# A title longer than this is prose that happened to contain a role word, not a title.
_MAX_TITLE_LEN = 80

# Inline separators used in one-line signatures: "Jane Reyes | VP, BD | Nexagen".
_SEPARATORS = re.compile(r"\s*[|•·‖]\s*|\s+[–—]\s+")


# --- where the quoted history starts ----------------------------------------------
# The earliest match wins; everything from there down belongs to an older message.
_QUOTE_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^-{2,}\s*original message\s*-{2,}", re.I | re.M),
    re.compile(r"^_{10,}\s*$", re.M),                      # Outlook's divider rule
    re.compile(r"^\s*on .{4,80}\bwrote:\s*$", re.I | re.M),  # "On 12 Jul, X wrote:"
    re.compile(r"^\s*from:\s.+$", re.I | re.M),            # forwarded header block
    re.compile(r"^\s*>", re.M),                            # plain-text quoting
    re.compile(r"^-{2,}\s*forwarded message\s*-{2,}", re.I | re.M),
)

# Trailing noise that sits BELOW a signature and would otherwise become the tail.
_TRAILER = re.compile(
    r"^\s*(sent from my \w+"
    r"|this (e-?mail|message) .*(confidential|intended)"
    r"|confidentiality notice"
    r"|please consider the environment"
    r"|unsubscribe\b).*$",
    re.I,
)

_PHONE = re.compile(
    r"""(?:\+?1[\s.\-]?)?          # optional US country code
        (?:\(\d{3}\)|\d{3})        # area code, with or without parens
        [\s.\-]\s?\d{3}[\s.\-]\d{4}   # prefix + line, separator required
        (?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?   # optional extension
    """,
    re.X | re.I,
)

# --- role vocabulary ---------------------------------------------------------------
# Ordered: the first match wins, so narrower patterns come first ("Senior Vice
# President" must not be read as "President").
_SENIORITY: tuple[tuple[str, str], ...] = (
    (r"senior vice president|\bsvp\b", "SVP"),
    (r"vice president|\bvp\b|\bevp\b", "VP"),
    (r"\bchief\b|\bc[teoifs]o\b|\bpresident\b|founder|co-?founder|\bowner\b|managing partner", "C-level"),
    (r"\bdirectors?\b|\bhead of\b", "Director"),
    (r"\bmanagers?\b|\bleads?\b|\bprincipal\b|\bsupervisor\b", "Manager"),
)

# Ordered by what actually matters for govcon BD: who can agree to team, and who owns
# the vehicle, rank above everything else.
#
# Two traps, both caught by scripts/test_signature.py:
#   * plurals — `\bprogram\b` does NOT match "Programs", so role nouns take `s?`.
#   * "Vice President" contains "President", which would file every VP under Executive.
#     The lookbehind keeps Executive for genuine C-suite; a VP's seniority already
#     carries the rank, so their FUNCTION should be their department.
_FUNCTION: tuple[tuple[str, str], ...] = (
    (r"business development|\bcapture\b|\bbd\b|\bgrowth\b|proposals?\b|\bbusdev\b", "BD & Capture"),
    (r"contract|procurement|acquisition|contracting officer|\bko\b|\bcor\b|\bcotr\b", "Contracts"),
    (r"\bprograms?\b|\bprojects?\b|\bpmo\b|portfolio", "Program"),
    (r"chief executive|chief operating|\bceo\b|\bcoo\b|(?<!vice[ -])\bpresident\b|founder|\bowner\b|managing partner", "Executive"),
    (r"engineer|architect|developer|scientist|technical|technolog|software|systems|\bcto\b|\bcio\b", "Technical"),
    (r"finance|financial|controller|\bcfo\b|accounting|pricing", "Finance"),
    (r"security|\bciso\b|\bisso\b|\bfso\b|cyber", "Security"),
)

# THE fail-closed guard: a candidate line is only ever treated as a job title if it
# contains one of these. Without it, "Thanks again for your time" becomes a job title.
_TITLE_HINT = re.compile(
    "|".join(p for p, _ in _SENIORITY)
    + "|"
    + "|".join(p for p, _ in _FUNCTION)
    + r"|\bofficers?\b|\banalysts?\b|\bspecialists?\b|\bconsultants?\b|\badministrators?\b"
    + r"|\bcoordinators?\b|\bassociates?\b|\bpartners?\b|\bcounsel\b|\brecruiters?\b",
    re.I,
)

# Lines that are never a title even though they may contain a role word.
#
# The prose rules below were added after running this over a real mailbox: the keyword
# guard alone happily accepted "I'd be happy to set up a meeting to walk through...",
# "Please find the attached Technical Writer resume", "Dear GSA Industry Partner:" and
# "• Navy NAVAIR Mission-Specific Network Engineering". A job title is a short NOUN
# PHRASE — it has no pronouns, no verbs of address, no bullet, and no trailing colon.
_NOT_TITLE = re.compile(
    r"@|https?://|www\.|\b(?:tel|fax|mobile|cell|phone|office|desk)\b\s*[:.]|"
    r"^\s*(?:best|kind(?:est)?|warm)\s+regards|^\s*(?:thanks|thank you|cheers|sincerely|respectfully)\b"
    # --- prose tells ---
    r"|^\s*[•·▪*>•▪]"                     # a bullet: this is body copy
    r"|[:?!]\s*$"                                    # a heading, a question, a shout
    r"|\b(?:i|i'?d|i'?m|we|we'?re|you|your|our|us|my|me|he|she|they)\b"   # pronouns
    r"|\b(?:please|dear|hi|hello|find|attached|happy|hope|looking|thanks|regards|"
    r"sent|see|let|would|could|should|will|can|need|want|set up|reach out)\b"
    # Notification nouns. None of these ever appear in a job title, but they sit next to
    # role words in newsletter subject lines: "CSO Reminder", "…Solutions Opening".
    r"|\b(?:reminder|newsletter|announcement|webinar|opening|digest|bulletin|"
    r"invitation|notification|alert|unsubscribe)\b",
    re.I,
)

# Mailboxes nobody works at. A `noreply@` address has no signature by definition, so
# anything that looks like one is a marketing footer being misread. Kept conservative on
# purpose: role addresses like `events@` or `info@` ARE sometimes typed by a real person
# who signs off properly, so only clearly-machine local parts are excluded.
_AUTOMATED_LOCAL = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|notifications?|mailer-?daemon|postmaster|"
    r"bounces?|auto-?confirm|automated|alerts?|no-?response)(?:[-+_.].*)?$",
    re.I,
)


def is_automated_address(email: str) -> bool:
    """True for machine mailboxes — nobody works there, so nothing they send is a signature."""
    local = (email or "").split("@", 1)[0].strip().lower()
    return bool(local) and bool(_AUTOMATED_LOCAL.match(local))

# A title is short. Anything longer is a sentence that happens to contain a role word.
_MAX_TITLE_WORDS = 8


@dataclass(frozen=True)
class SignatureFacts:
    name: Optional[str] = None
    title: Optional[str] = None
    phone: Optional[str] = None
    seniority: Optional[str] = None
    function: Optional[str] = None

    def is_useful(self) -> bool:
        """True when there is something worth recording as a fact."""
        return bool(self.title or self.phone)


class _TextExtractor(HTMLParser):
    """Minimal HTML -> text. Stdlib only, deliberately: adding a parser dependency to
    read a signature is not a trade worth making."""

    _BREAK = {"br", "p", "div", "tr", "li", "table", "h1", "h2", "h3"}
    _DROP = {"style", "script", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self._DROP:
            self._depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._DROP and self._depth:
            self._depth -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup must never break ingestion
        return re.sub(r"<[^>]+>", " ", html)
    return "".join(parser.parts)


def strip_quoted(body: str) -> str:
    """Everything above the first sign of an older message.

    Load-bearing: without this the 'tail' of a reply chain is the oldest message, and
    we would attribute a stranger's signature to this contact.
    """
    cut = len(body)
    for marker in _QUOTE_MARKERS:
        found = marker.search(body)
        if found and found.start() < cut:
            cut = found.start()
    return body[:cut]


def _lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.replace("\xa0", " ").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line and not _TRAILER.match(line):
            out.append(line)
    return out


def _candidates(lines: Sequence[str]) -> list[str]:
    """Tail lines, with pipe-separated SEGMENTS offered before the whole line.

    Order matters: real signatures are commonly "Rajesh Parikh | COO". Offering the whole
    line first returns the name and the title glued together; offering segments first
    lets the name fall through the keyword guard and the title win on its own.
    """
    out: list[str] = []
    for line in lines[-_TAIL_LINES:]:
        parts = [p.strip() for p in _SEPARATORS.split(line) if p and p.strip()]
        if len(parts) > 1:
            out.extend(parts)
        out.append(line)
    return out


def derive_seniority(title: Optional[str]) -> Optional[str]:
    """'VP, Business Development' -> 'VP'. Every real title gets a band; IC is the floor."""
    if not title:
        return None
    for pattern, band in _SENIORITY:
        if re.search(pattern, title, re.I):
            return band
    return "IC"


def derive_function(title: Optional[str]) -> Optional[str]:
    """'VP, Business Development' -> 'BD & Capture'. This is the field that answers
    'can this person actually help us win work?'"""
    if not title:
        return None
    for pattern, area in _FUNCTION:
        if re.search(pattern, title, re.I):
            return area
    return "Other"


def _pick_title(candidates: Sequence[str]) -> Optional[str]:
    for line in candidates:
        if len(line) > _MAX_TITLE_LEN or len(line.split()) > _MAX_TITLE_WORDS:
            continue
        if _NOT_TITLE.search(line):
            continue
        if _TITLE_HINT.search(line):
            return line.strip(" ,;-–—|")
    return None


def _pick_phone(lines: Sequence[str]) -> Optional[str]:
    fallback: Optional[str] = None
    for line in lines:
        found = _PHONE.search(line)
        if not found:
            continue
        number = re.sub(r"\s+", " ", found.group(0)).strip()
        if re.search(r"\bfax\b", line, re.I):
            fallback = fallback or number  # only if there is nothing better
            continue
        return number
    return fallback


def _name_tokens(email: str) -> set[str]:
    local = (email or "").split("@", 1)[0].lower()
    return {t for t in re.split(r"[._\-+0-9]+", local) if len(t) > 2}


def _pick_name(lines: Sequence[str], sender_email: str) -> Optional[str]:
    """A line of 2-4 capitalised words whose tokens overlap the address local part.

    The overlap check is what stops a colleague's name (or a company name) being taken
    as the contact's: guess where to look, never guess what you will find.
    """
    tokens = _name_tokens(sender_email)
    for line in lines:
        if _NOT_TITLE.search(line) or _TITLE_HINT.search(line):
            continue
        words = line.split()
        if not (2 <= len(words) <= 4):
            continue
        if not all(re.fullmatch(r"[A-Z][A-Za-z'’\-\.]+,?", w) for w in words):
            continue
        if tokens and not any(w.strip(",.").lower() in tokens for w in words):
            continue
        return line.strip(" ,")
    return None


def extract_signature(
    body: Optional[str], sender_email: str, is_html: Optional[bool] = None
) -> Optional[SignatureFacts]:
    """Pull what a signature block states. Returns None when there is nothing solid.

    `is_html` is auto-detected when not given. A None return is a perfectly good
    outcome — most short replies carry no signature, and a blank field beats a guess.
    """
    if not body or not body.strip():
        return None
    # A machine mailbox has no job title. Skipping these outright removes a whole class
    # of false positives that no amount of line-level filtering catches reliably.
    if is_automated_address(sender_email):
        return None

    text = body
    if is_html or (is_html is None and re.search(r"<(?:br|p|div|table|span)\b", body, re.I)):
        text = html_to_text(body)

    lines = _lines(strip_quoted(text))
    if not lines:
        return None

    tail = lines[-_TAIL_LINES:]
    title = _pick_title(_candidates(lines))
    facts = SignatureFacts(
        name=_pick_name(tail, sender_email),
        title=title,
        phone=_pick_phone(tail),
        seniority=derive_seniority(title),
        function=derive_function(title),
    )
    return facts if facts.is_useful() else None


def evidence_detail(facts: SignatureFacts, received_at: Optional[str] = None) -> str:
    """The tooltip a BD rep reads. Written for them, not for a log:
    'their signature on 14 Jul reads "VP, Business Development"'."""
    when = f" on {received_at}" if received_at else ""
    said = facts.title or facts.phone or ""
    return f'their signature{when} reads "{said}"'
