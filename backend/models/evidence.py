"""Evidence — how sure are we about a fact, and why.

The rule this module exists to enforce: **an agent never sets a confidence score.**
It reports WHAT IT SAW, using a `kind` from the closed vocabulary below, and this
module prices it. Models grade their own certainty badly — they inflate it to look
useful — so the judgement is taken away from them and done in arithmetic here.

    agent says:  "their signature says VP of Contracts"   -> kind="outlook.signature-block"
    this module: 0.80, no corroboration -> PROBABLE       -> stored as a SUGGESTION
    agent says:  "...and they replied to us on a thread"  -> + kind="outlook.thread-reply"
    this module: 0.97, has a primary source -> VERIFIED   -> WRITTEN to the record

Why it matters here: a confidently wrong fact about a contracting officer or a teaming
partner is worse than a blank field, because nobody can tell it is wrong. A blank field
a BD rep fills in five seconds; a plausible-looking wrong title survives to the call.

THREE PROPERTIES THAT ARE NOT ACCIDENTS
  * Noisy-OR, not a sum or an average. Independent sources each chip away at the doubt
    that remains, so two weak signals add up to more than either alone — but never to
    certainty (hard ceiling at 0.99).
  * VERIFIED requires a PRIMARY source. A mountain of web citations can never write to
    a record; only something that identifies THIS subject can. This is the gate that
    stops "three blogs agree" from becoming a fact.
  * A contradiction HOLDS the fact (caps it at 0.45) rather than averaging it away.
    A signature saying one employer and a SAM.gov notice saying another is not 60% true,
    it is unresolved, and a human should see it that way.

See docs/enrichment-implementation-plan.md §5.1. Ported from trycompai/crm's evidence
ledger (docs/trycompai-deep-dive/reference/lib-evidence.ts + skill-evidence.md), with the
kinds re-cut for govcon sources (SAM.gov, Outlook, SharePoint) instead of Gmail/GitHub.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, Optional, Sequence, TypedDict

# --- the vocabulary ---------------------------------------------------------------
# An agent may ONLY report one of these. Anything else is ignored (fail closed).
EvidenceKind = Literal[
    # PRIMARY — the source identifies THIS subject, so it can carry a fact on its own.
    "samgov.entity-record",
    "sam.poc-listed",
    "outlook.thread-reply",
    "gov-domain-rule",
    "outlook.signature-block",
    "pdl.domain-company",
    "sharepoint.authored-doc",
    "outlook.meeting-attend",
    "company.own-website",
    # SUPPORTING — true, but consistent with many people/companies, so never enough alone.
    "web.cited-claim",
    "outlook.address-book",
    "handle.name-form",
    "domain-derived-name",
    "employer-only",
    # SPECIAL — records that two sources disagree.
    "contradiction",
]

Band = Literal["VERIFIED", "PROBABLE", "POSSIBLE"]


@dataclass(frozen=True)
class Weighting:
    weight: float
    primary: bool
    label: str  # reads inside a sentence: "Verified because <label> and <label>."


WEIGHTS: dict[EvidenceKind, Weighting] = {
    # --- primary ------------------------------------------------------------------
    "samgov.entity-record": Weighting(
        0.90, True, "the company's own SAM.gov registration says so"
    ),
    "sam.poc-listed": Weighting(
        0.90, True, "they are the named point of contact on a SAM.gov notice"
    ),
    "outlook.thread-reply": Weighting(
        0.85, True, "they replied to us from that address"
    ),
    # A deterministic lookup table (.mil/.gov -> agency), not a guess: us.army.mil IS the
    # US Army. Primary because the domain itself identifies the organisation.
    "gov-domain-rule": Weighting(
        0.85, True, "the .gov/.mil domain identifies the agency"
    ),
    # The best source there is for a job title: people update a signature the week they
    # are promoted, which is more than can be said for any profile or data vendor.
    "outlook.signature-block": Weighting(
        0.80, True, "their own email signature says so"
    ),
    # At the VERIFIED floor deliberately: a curated domain->company dataset match is a
    # LOOKUP, not a guess — @raytheon.com is Raytheon. This is the line that separates a
    # dataset hit (a fact) from a name derived off the domain (a suggestion, 0.30 below).
    "pdl.domain-company": Weighting(
        0.85, True, "the email domain matched the company dataset"
    ),
    "sharepoint.authored-doc": Weighting(
        0.75, True, "they authored a document in our SharePoint"
    ),
    "outlook.meeting-attend": Weighting(
        0.70, True, "they accepted a meeting on our calendar"
    ),
    # A company describing its own business, on its own domain, is the authoritative
    # source for what that company does — the corporate equivalent of a signature block.
    # Whether a page IS the company's own site is decided in CODE by comparing hosts
    # (see tasks/agent_tasks.py::_is_own_site), never by asking the model to grade itself.
    "company.own-website": Weighting(
        0.85, True, "the company's own website says so"
    ),
    # --- supporting ---------------------------------------------------------------
    "web.cited-claim": Weighting(0.40, False, "a cited web source states it"),
    "outlook.address-book": Weighting(
        0.35, False, "it is saved on their Outlook contact card"
    ),
    # Weak on purpose: jsmith@ is a form of every J. Smith's name.
    "handle.name-form": Weighting(0.35, False, "the address is a form of their name"),
    # This is the silent guess we are trying to stop asserting: raytheon.com -> "Raytheon".
    # Often right, occasionally embarrassing, never a fact on its own.
    "domain-derived-name": Weighting(
        0.30, False, "the company name was derived from the email domain"
    ),
    # Nearly worthless alone, deliberately: this is how a colleague gets filed as the contact.
    "employer-only": Weighting(0.20, False, "the employer matches, the name does not"),
    # --- special ------------------------------------------------------------------
    "contradiction": Weighting(0.00, False, "another source disagrees"),
}

PRIMARY_KINDS = frozenset(k for k, w in WEIGHTS.items() if w.primary)


class Evidence(TypedDict):
    """One observation. `detail` is read by a human in a tooltip — write it for them:
    good: 'their signature on 14 Jul reads "VP, Business Development"'
    bad:  'signature match confirmed'
    """

    kind: EvidenceKind
    detail: str
    source_url: NotRequired[str]


@dataclass(frozen=True)
class Scored:
    score: float
    band: Optional[Band]  # None => too weak to store at all
    has_primary: bool
    rationale: str


# Nothing is ever certain; a run of strong sources still leaves a sliver of doubt.
CEILING = 0.99

# A clash caps the score here — high enough to still be offered to a human as
# "sources disagree", far too low to ever be written to a record.
CONTRADICTED = 0.45

BAND_FLOOR: dict[Band, float] = {"VERIFIED": 0.85, "PROBABLE": 0.55, "POSSIBLE": 0.30}


def _usable(evidence: Sequence[Evidence]) -> list[Evidence]:
    """Drop entries we cannot trust, and collapse double-counted ones.

    Two guards, both fail-closed:
      * An unknown `kind` (a model inventing vocabulary) is ignored rather than raising.
      * `web.cited-claim` without a `source_url` is ignored — an uncited web claim is the
        exact shape of a hallucination, and the skill requires the URL.

    Then dedupe on (kind, source_url): two facts read off the SAME page are ONE
    observation, not two. Splitting them would double-count a single source into false
    certainty, which is the arithmetic this module exists to prevent.
    """
    out: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for item in evidence:
        kind = item.get("kind")
        if kind not in WEIGHTS:
            continue
        url = (item.get("source_url") or "").strip()
        if kind == "web.cited-claim" and not url:
            continue
        if url:
            key = (kind, url)
            if key in seen:
                continue
            seen.add(key)
        out.append(item)
    return out


def score_evidence(evidence: Sequence[Evidence]) -> Scored:
    """Price a list of observations into a score, a band, and a human-readable reason."""
    items = _usable(evidence)
    if not items:
        return Scored(0.0, None, False, "No evidence.")

    contradicted = any(i["kind"] == "contradiction" for i in items)
    has_primary = any(i["kind"] in PRIMARY_KINDS for i in items)

    # Noisy-OR: start at "totally unknown" and let each independent source remove a
    # fraction of the doubt that is LEFT. Order-independent, and it can never hit 1.0.
    remaining = 1.0
    for item in items:
        remaining *= 1.0 - WEIGHTS[item["kind"]].weight

    score = min(CEILING, 1.0 - remaining)
    if contradicted:
        score = min(score, CONTRADICTED)

    return Scored(
        score=score,
        band=band_for(score, has_primary),
        has_primary=has_primary,
        rationale=_rationale(items, contradicted, has_primary),
    )


def band_for(score: float, has_primary: bool) -> Optional[Band]:
    """VERIFIED additionally requires a primary source — no pile of weak, corroborating
    sources may ever write to a record, however many of them there are."""
    if score >= BAND_FLOOR["VERIFIED"] and has_primary:
        return "VERIFIED"
    if score >= BAND_FLOOR["PROBABLE"]:
        return "PROBABLE"
    if score >= BAND_FLOOR["POSSIBLE"]:
        return "POSSIBLE"
    return None


def is_writable(scored: Scored) -> bool:
    """True only when this may be written onto the record itself (vs offered as a
    suggestion). The single place callers should ask that question."""
    return scored.band == "VERIFIED"


def _rationale(
    items: Sequence[Evidence], contradicted: bool, has_primary: bool
) -> str:
    if contradicted:
        clash = next((i for i in items if i["kind"] == "contradiction"), None)
        detail = (clash or {}).get("detail") or "sources disagree"
        return f"Held: {detail}."

    reasons = [
        WEIGHTS[i["kind"]].label for i in items if i["kind"] != "contradiction"
    ]
    if not reasons:
        return "No supporting evidence."

    listed = _join(reasons)
    if has_primary:
        return _capitalise(listed) + "."
    # Spelled out, because this is the difference between a suggestion and a fact.
    return f"{_capitalise(listed)} — but nothing that identifies them directly."


def _join(words: Sequence[str]) -> str:
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _capitalise(value: str) -> str:
    return value[:1].upper() + value[1:] if value else value
