"""The proof gate — the checks a drafted nudge must pass before it can reach a rep.

This is the piece that makes one-tap approve safe. The rep is going to send this with a
single tap and (mostly) a glance, so the draft has to be verified BEFORE it ever becomes a
card — not trusted because the model sounded confident. The governing rule, borrowed from the
delegation-loop discipline: *if a check cannot fail, it is not a check.* Every check below
produces a verdict from evidence outside the draft's own say-so, and every one can fail on a
real bad draft:

  recipient_valid   the address we would send to is a real address
  no_foreign_email  the body names no OTHER email address (the wrong-recipient guard)
  no_placeholders   no "[Your Name]"-style fill-in-the-blank survived into subject/body
  subject_present   there is a subject, and it is not a paragraph
  body_length       the body is a short note, neither empty nor a wall
  grounded          every specific the draft says it "drew on" is a fact we actually hold
  no_invented_specifics  no proper noun in the body that we cannot account for
  personal_hook     a personal nudge references a real stored fact (not a generic hello)

Deterministic and model-free on purpose: the same draft yields the same verdict every time,
and the reasons are inspectable. A failed gate DROPS the draft — it never reaches the rep.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A leftover fill-in-the-blank: "[Your Name]", "[Company]", "[date]". A bracketed run of
# letters/spaces the model left for a human to complete. We do NOT match "[1]" or "[#3]"
# (citation-ish, not a form field) — the marker is alphabetic content inside the brackets.
_PLACEHOLDER_RE = re.compile(r"\[[^\]]*[A-Za-z][^\]]*\]")

# A capitalised word mid-sentence is the shape a fabricated specific takes ("the Aspen trip",
# "the Denver office"). We can only judge those we cannot account for, so everything the
# draft is legitimately allowed to name is collected into an allowlist first.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?\n])\s+")

# Capitalised words that carry no factual claim: greetings, calendar words, sign-offs and the
# ordinary capitalised English a note contains. Listing these is what keeps the check from
# firing on every polite sentence.
_INNOCUOUS = frozenset("""
i i'm i've i'd i'll hi hello hey dear thanks thank best regards cheers sincerely warmly
monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december
mon tue wed thu fri sat sun jan feb mar apr jun jul aug sep sept oct nov dec
happy new year holidays christmas thanksgiving
it is was the a an and or but if so as at by for from in of on to with we you your yours
our ours us me my mine he she they them their this that these those there here
hope hoping wanted just wondering wondered been being have has had do does did
let know think thought see saw good great nice well all any some
""".split())


def _proper_nouns(text: str) -> list[str]:
    """Capitalised words that are NOT sentence-initial — the shape of a named specific.

    Sentence-initial words are skipped because every sentence starts capitalised, so they
    carry no signal. Everything else capitalised mid-sentence is a candidate claim.
    """
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        words = _WORD_RE.findall(sentence)
        for i, w in enumerate(words):
            if i == 0:
                continue  # sentence-initial capital carries no meaning
            if w[:1].isupper() and w.lower() not in _INNOCUOUS:
                found.append(w)
    return found

# A relationship note is short. These bounds catch the empty draft and the essay alike.
_MIN_WORDS = 3
_MAX_WORDS = 160
_MAX_SUBJECT = 120


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ProofResult:
    ok: bool
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def summary(self) -> str:
        """One line per check — the pass/fail-with-evidence the rep (or a log) can read."""
        return "\n".join(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in self.checks)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _grounded_in(claim: str, known: Sequence[str]) -> bool:
    """A drew-on claim is grounded if it matches a known fact either way round — 'golf'
    grounds against 'plays golf', and 'played golf 2026-08-03' grounds 'golf'. Substring
    both directions tolerates the light paraphrase a model applies without letting an
    ungrounded specific through."""
    c = _norm(claim)
    if not c:
        return False
    for k in known:
        kn = _norm(k)
        if not kn:
            continue
        if c == kn or c in kn or kn in c:
            return True
    return False


def verify_nudge(
    *,
    kind: str,
    recipient_email: str,
    subject: str,
    body: str,
    drew_on: Iterable[str],
    known_facts: Iterable[str],
    context: Iterable[str] = (),
) -> ProofResult:
    """Run every check on one drafted nudge.

    `known_facts` is the vocabulary the draft is allowed to reference — the stored personal
    facts we actually hold for this contact (values, verbatim). `context` is everything else
    it may legitimately name: the contact's name and company, the sender's name, our own
    company, the subject of their last exchange. Together they are the allowlist the
    invented-specifics check measures the body against. Returns a ProofResult; `.ok` is the gate.
    """
    recipient = (recipient_email or "").strip()
    subj = (subject or "").strip()
    text = (body or "").strip()
    drew = [d for d in (drew_on or []) if d and d.strip()]
    known = [k for k in (known_facts or []) if k and k.strip()]
    context_words = [c for c in (context or []) if c and str(c).strip()]
    checks: list[Check] = []

    # recipient_valid — we must have a real address to send to.
    valid_recipient = bool(_EMAIL_RE.fullmatch(recipient))
    checks.append(Check(
        "recipient_valid", valid_recipient,
        f"recipient is {recipient!r}" if valid_recipient else f"{recipient!r} is not a valid email",
    ))

    # no_foreign_email — the body must not address some OTHER email address. This is the
    # wrong-recipient guard: a hallucinated or copied-in address is caught here, not by the rep.
    foreign = [e for e in _EMAIL_RE.findall(text) if e.strip().lower() != recipient.lower()]
    checks.append(Check(
        "no_foreign_email", not foreign,
        "no other address in the body" if not foreign else f"body names other address(es): {foreign}",
    ))

    # no_placeholders — a "[Your Name]" that survived is a template mailed to a customer on
    # the one-tap send. Check subject AND body; the sign-off placeholder lives in the body.
    placeholders = _PLACEHOLDER_RE.findall(subj) + _PLACEHOLDER_RE.findall(text)
    checks.append(Check(
        "no_placeholders", not placeholders,
        "no fill-in-the-blanks left" if not placeholders
        else f"unfilled placeholder(s): {placeholders}",
    ))

    # subject_present — there is a subject and it is a subject, not a paragraph.
    subj_ok = bool(subj) and len(subj) <= _MAX_SUBJECT
    checks.append(Check(
        "subject_present", subj_ok,
        f"{len(subj)} chars" if subj else "no subject",
    ))

    # body_length — a short note, neither empty nor a wall.
    words = len(text.split())
    len_ok = _MIN_WORDS <= words <= _MAX_WORDS
    checks.append(Check(
        "body_length", len_ok,
        f"{words} words (allowed {_MIN_WORDS}-{_MAX_WORDS})",
    ))

    # grounded — every specific the draft claims it drew on must be a fact we hold. A draft
    # that cites a golf game we have no record of fails here.
    ungrounded = [d for d in drew if not _grounded_in(d, known)]
    checks.append(Check(
        "grounded", not ungrounded,
        "every cited fact is on record" if not ungrounded
        else f"cites fact(s) we do not hold: {ungrounded}",
    ))

    # no_invented_specifics — the `grounded` check above can only test what the model ADMITS
    # to using. A body that says "hope the Aspen trip was great" while declaring
    # drew_on=["golf"] passes every other check. So: any proper noun in the body must be
    # accountable to something we actually know — the contact, their company, the sender, our
    # own company, or a stored fact. Anything else is a specific the model invented.
    allowed_words: set[str] = set()
    # The recipient's own address is ALWAYS known, and it is where a contact's name and
    # employer usually live ("dave@lockheed.com" -> dave, lockheed). Deriving from it means
    # the check still works for a contact whose name/company we never stored — without it,
    # a sparse contact would have every draft gated out for naming the person it is written to.
    for w in _WORD_RE.findall(recipient.replace("@", " ").replace(".", " ")):
        allowed_words.add(w.lower())
    for source in (*known, *context_words):
        for w in _WORD_RE.findall(str(source) or ""):
            allowed_words.add(w.lower())
    invented = sorted({
        w for w in _proper_nouns(text) if w.lower() not in allowed_words
    })
    checks.append(Check(
        "no_invented_specifics", not invented,
        "every named specific is accounted for" if not invented
        else f"names we cannot account for: {invented}",
    ))

    # personal_hook — a PERSONAL nudge exists because of a real hook, so it must reference
    # one: either it grounded a drew-on fact, or a known fact appears in the body. A generic
    # "just checking in" is fine for a going-cold touch, but not for a personal-hook card.
    if kind == "relationship_personal":
        grounded_claim = any(_grounded_in(d, known) for d in drew)
        fact_in_body = any(_norm(k) and _norm(k) in _norm(text) for k in known)
        hook_ok = bool(known) and (grounded_claim or fact_in_body)
        checks.append(Check(
            "personal_hook", hook_ok,
            "references a real personal fact" if hook_ok
            else "personal nudge with no grounded personal fact",
        ))

    return ProofResult(ok=all(c.ok for c in checks), checks=checks)
