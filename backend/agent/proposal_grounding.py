"""Make a written proposal document stand on the company's own library, and read like a person.

The writer is told never to type a company fact, and this is where that is enforced:

1. FACTS COME FROM RECORDS. A reference names a contract number and a person a name; their
   customer, value, dates, role, contact, clearance, certifications and education are copied
   here from the library's records. A missing field becomes [NEEDS: …], never the model's guess.
2. CLAIMS CARRY PROOF. Each claim about past work cites a library file and quotes it, and the quote
   is checked against THAT file alone. A claim about the Army contract has to be in the Army file:
   true somewhere else in the library is not good enough.
3. NOTHING DISAPPEARS. A claim that fails, an uncited sentence stating a company fact, and a note
   the model left for itself are wrapped in a highlighted [CHECK: …] and listed for the reviewer.
   Deleting them would hide the problem; keeping them quietly would send it to the government.
"""
from __future__ import annotations

import re

from agent.library_agent import _tokens
from agent.requirements_agent import _quoted
from client.library_store import get_library_store

_CITE = re.compile(r"\s*\[\[([^|\]]*)\|([^\]]*)\]\]")
# A sentence ends at . ! ? — but not after an initial ("U.S.", "J."), a title or "Inc.".
_SENTENCE = re.compile(r"(?<!\b[A-Z]\.)(?<!\bInc\.)(?<!\bMaj\.)(?<!\bLt\.)(?<!\bCol\.)(?<!\bDr\.)"
                       r"(?<!\bMr\.)(?<!\bMs\.)(?<!\bNo\.)(?<!\bvs\.)(?<!\be\.g\.)(?<!\bi\.e\.)"
                       r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"“§])")
_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")

# What the company did before, said as fact. Present-tense approach text ("Tier 1 resolves…")
# is the writer's job; past results and "currently" claims are the library's.
_PAST_CLAIM = re.compile(
    r"\b(delivered|performed|supported|managed|migrated|reduced|achieved|sustained|completed|"
    r"provided|operated|maintained|implemented|deployed|awarded|served|"
    r"currently (?:operates|maintains|supports|holds|provides|serves|manages))\b", re.I)

# Notes the model leaves for itself, and lines that talk about the document instead of the work.
_SELF_NOTE = re.compile(
    r"\bper (?:the |their |his |her )?(?:resume|library|record|source|file)s?\b"
    r"|\b(?:in|from) (?:the|our|your) (?:company )?library\b"
    r"|\bnot (?:stated|specified|provided|defined|included|identified) in the "
    r"(?:solicitation|rfq|rfp|rfi|sow|pws|statement of work)\b"
    r"|\b(?:up)?on request\b"
    r"|\bthis (?:volume|document|section|proposal|response) (?:describes|addresses|outlines|presents|provides)\b"
    r"|\bour (?:company )?(?:library|records|files)\b|\b(?:library|records) (?:does|do|did) not\b"
    r"|\bnot (?:yet )?(?:in|recorded in|documented in) (?:the|our) (?:library|records|files)\b"
    r"|\b(?:does|do|will) not (?:yet )?claim\b|\b(?:available|exists?) (?:for \w+ )?to cite\b",
    re.I)

_FILLER = re.compile(
    r"\b(robust|seamless(?:ly)?|leverag(?:e|es|ed|ing)|cutting[- ]edge|state[- ]of[- ]the[- ]art|"
    r"best[- ]in[- ]class|world[- ]class|holistic|synerg(?:y|ies)|unparalleled|game[- ]chang\w*|"
    r"comprehensive solution|we are excited)\b", re.I)


# Words that carry no claim of their own: common sentence words, roles and generic government terms.
_PLAIN = set("""a an the and or of for to in on at by with from as is are was were be been has have had
it its this that these those our we us they their his her he she who which what when where how
government federal agency department office contract contractor contracts program programs manager
management project service services support desk help team staff customer customers prime
subcontractor officer contracting representative cor ko co user users system systems work
tier delivered operates maintains supports holds provides served managed performed completed
reduced sustained achieved implemented deployed migrated configured led leads lead current
currently prior previous total annual all each both over more than across including inc llc corp
january february march april may june july august september october november december""".split())


def _terms(text: str, company: str) -> set[str]:
    """What a claim actually asserts beyond its grammar: names, products, acronyms and certificates
    (ServiceNow, ITIL, CISA, Army, PMP). Each must be in the cited part of the file."""
    own = set(_tokens(company).split())
    words = re.findall(r"\b[A-Za-z][A-Za-z0-9&/-]*\b", text or "")
    out = set()
    for i, w in enumerate(words):
        distinct = (any(ch.isupper() for ch in w[1:]) or (w[0].isupper() and i > 0))
        for t in _tokens(w).split():
            if distinct and t not in _PLAIN and t not in own and not t.isdigit() and len(t) > 1:
                out.add(t)
    return out


def _numbers(text: str | None) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _DIGITS.findall(text or "")}


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _money(v) -> str | None:
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return None


def _needs(what: str) -> str:
    return f"[NEEDS: {what}]"


# Words a sentence can't end on when its object was in a removed citation marker.
_OPEN_END = re.compile(r"(?:^|\b(?:following|hold|holds|held|include|includes|including|maintain|"
                       r"maintains|possess|possesses|are|is|was|were|of|with|for|to|as|by|the|a|an|"
                       r"and|or|from|under|namely|such as)|:)\s*[.:]?$", re.I)


def _contract_anchors(number: str, title: str) -> list[str]:
    """How a file might name a contract: each piece of its number (with and without dashes),
    its title, and the acronym in its title ("... Center (INOC)")."""
    out: list[str] = []
    for n in re.split(r"[/,;]|\s{2,}|\s+(?=[A-Z0-9]{6,})", number):
        n = n.strip()
        if len(n) >= 6:
            out += [n, n.replace("-", "")]
    if len(title) >= 12:
        out.append(title)
    out += re.findall(r"\(([A-Z][A-Z0-9-]{2,})\)", title)
    return list(dict.fromkeys(out))


def _anchor_re(anchor: str) -> str:
    return rf"(?<![a-z0-9]){re.escape(anchor.lower())}(?![a-z0-9])"


def _dangling(plain: str) -> bool:
    """True when what's left after removing a sentence's markers has lost its content: nothing
    but punctuation, or a sentence that stops where its object should be."""
    return not re.search(r"\w", plain) or bool(_OPEN_END.search(plain.strip()))


def _with_quotes(plain: str, quotes: list[str]) -> str:
    body = "; ".join(q.strip().rstrip(".") for q in quotes if q.strip())
    if not body:
        return plain
    if not re.search(r"\w", plain):
        return body[0].upper() + body[1:] + "."
    head = plain.strip().rstrip(".:").rstrip()
    joiner = ": " if re.search(r"\bfollowing$", head, re.I) else " "
    return f"{head}{joiner}{body}."


def _flag(text: str) -> str:
    text = text.strip()
    return text if text.startswith("[CHECK:") else f"[CHECK: {text}]"


class _Grounder:
    def __init__(self, organization_id: str, employee_email: str | None, solicitation: str,
                 company: str, notes: str = ""):
        self.org = organization_id
        self.store = get_library_store()
        self.rows = {r["item_id"]: r for r in self.store.analysed(organization_id, email=employee_email)}
        self.contracts = {}
        self.people = {}
        for r in self.rows.values():
            for c in r.get("contracts") or []:
                if c.get("contract_number"):
                    self.contracts.setdefault(_norm(c["contract_number"]), (c, r))
            for p in r.get("people") or []:
                if p.get("name"):
                    self.people.setdefault(_norm(p["name"]), (p, r))
        # What the person told the writer counts like the solicitation: restating it isn't an
        # unsourced claim, and a paragraph may cite it as the file `notes`.
        self.solicitation = _tokens(f"{solicitation} {notes}")
        self.company = company
        self._texts: dict[str, tuple[str, set[str]]] = {}
        self._parts: dict[tuple[str, str], tuple[str, set[str]]] = {}
        self._everything: str | None = None
        self.gaps: list[str] = []
        self.prose_sources = set(self.rows)
        if notes.strip():
            self._texts["notes"] = (_tokens(notes), _numbers(notes))
            self.prose_sources.add("notes")

    def _load(self, item_id: str) -> tuple[str, set[str]]:
        """A file's words, and the numbers in it ("24x7" holds 24 and 7; "6,500" is 6500)."""
        if item_id not in self._texts:
            raw = self.store.source_text(self.org, item_id)
            self._texts[item_id] = (_tokens(raw), _numbers(raw))
        return self._texts[item_id]

    def _text(self, item_id: str) -> str:
        return self._load(item_id)[0]

    def _part(self, item_id: str, anchors: list[str], others: list[str]) -> tuple[str, set[str]]:
        """The part of a file about ONE contract or person: from where its name (or number) first
        appears to where the next one in the same file begins. A past-performance file with two
        contracts, or a resume book with two people, must not lend one's facts to the other."""
        key = (item_id, "|".join(anchors))
        if key in self._parts:
            return self._parts[key]
        raw = self.store.source_text(self.org, item_id) or ""
        low = raw.lower()
        hits = [low.find(a.lower()) for a in anchors if a and a.lower() in low]
        part = raw
        if hits:
            start = min(hits)
            nxt = [p for o in others if o and (p := low.find(o.lower(), start + 1)) > start]
            # The heading above a reference ("Reference 2: …") belongs to it, so step back a line.
            start = max(0, low.rfind("\n", 0, max(0, start - 1)))
            part = raw[start: min(nxt)] if nxt else raw[start:]
        self._parts[key] = (_tokens(part), _numbers(part))
        return self._parts[key]

    def _library(self) -> str:
        """Every readable file at once, for a sentence that states a fact without saying where
        it came from. Capped: past that, a sentence has to cite its file."""
        if self._everything is None:
            parts, size = [], 0
            for item_id in self.rows:
                t = self._text(item_id)
                if size + len(t) > 3_000_000:
                    break
                parts.append(t)
                size += len(t)
            self._everything = " | ".join(parts)
        return self._everything

    def _name(self, item_id: str) -> str:
        return (self.rows.get(item_id) or {}).get("name") or "the library"

    # ---- proof -------------------------------------------------------------------------
    def proven(self, text: str, source_id: str, quote: str, allowed: set[str],
               part: tuple[str, set[str]] | None = None) -> bool:
        """The file is one this claim may cite, the quote is in it, and what the claim asserts is
        there too: every number and every distinctive name or term. A real quote can't carry an
        invented figure, product or customer, and `part` narrows it all to one contract or person."""
        if not source_id or source_id not in allowed:
            return False
        words, numbers = part or self._load(source_id)
        if not _quoted(quote, words) or not _numbers(text) <= numbers:
            return False
        return all(f" {t} " in f" {words} " for t in _terms(text, self.company))

    def cited(self, c, allowed: set[str], where: str, part: tuple[str, set[str]] | None = None) -> None:
        """Check one Cited claim in place."""
        if not c.text.strip() or c.text.lstrip().startswith(("[NEEDS:", "[CHECK:")):
            return
        if self.proven(c.text, c.source_id, c.quote, allowed, part):
            c.text = self.style(c.text)
            return
        place = self._name(c.source_id) if c.source_id in self.rows else where
        self.gaps.append(f'Not backed by {place}: "{c.text.strip()[:160]}"')
        c.text = _flag(c.text)

    # ---- prose -------------------------------------------------------------------------
    def prose(self, text: str) -> str:
        """Check the [[file|quote]] citations in a paragraph, flag company facts stated without
        one, and strip the markers."""
        marks: list[tuple[str, str]] = []

        def keep(m: re.Match) -> str:
            marks.append((m.group(1).strip(), m.group(2).strip()))
            return f" §{len(marks) - 1}§"

        sentences = _SENTENCE.split(_CITE.sub(keep, text))
        # A citation written after the full stop ("…users. [[id|…]]") belongs to the sentence before.
        for i in range(1, len(sentences)):
            lead = re.match(r"^((?:\s*§\d+§)+)", sentences[i])
            if lead:
                sentences[i - 1] += lead.group(1)
                sentences[i] = sentences[i][lead.end():]
        def inline(s: str) -> str:
            """'We also hold [[id|ISO 27001]] status.': the marker sits where words belong."""
            def put(m: re.Match) -> str:
                before, after = s[:m.start()], s[m.end():]
                if re.search(r"\w", after) and _OPEN_END.search(before.rstrip()):
                    return " " + marks[int(m.group(1))][1].strip().rstrip(".")
                return m.group(0)
            return re.sub(r"\s*§(\d+)§", put, s)

        out = []
        for s in sentences:
            refs = [marks[int(k)] for k in re.findall(r"§(\d+)§", s)]
            s = inline(s)
            plain = re.sub(r"\s*§\d+§", "", s).strip()
            if refs and _dangling(plain):
                # The writer put the fact INSIDE the marker ("We hold the following. [[id|CMMI L3
                # and ISO 9001]]", or a table cell that is only a marker). Deleting the marker
                # would delete the fact, so its quoted words become the text.
                plain = _with_quotes(plain, [q for _, q in refs])
            if not re.search(r"\w", plain):  # only the full stop a moved marker left behind
                if out and plain:
                    out[-1] = out[-1].rstrip(".") + plain
                continue
            if refs:
                if not any(self.proven(plain, sid, q, self.prose_sources) for sid, q in refs):
                    self.gaps.append(f'Not backed by the cited file: "{plain[:160]}"')
                    plain = _flag(plain)
            elif self._uncited_fact(plain):
                self.gaps.append(f'States a company fact with no library source: "{plain[:160]}"')
                plain = _flag(plain)
            out.append(self.style(plain))
        # What a removed marker leaves behind: "scale , and" / "work ."
        text = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"([.,;:])\s*,", r"\1", " ".join(out)))
        return re.sub(r"\.{2,}", ".", text)

    def _uncited_fact(self, sentence: str) -> bool:
        if sentence.startswith(("[NEEDS:", "[CHECK:")) or not _PAST_CLAIM.search(sentence):
            return False
        # A past-tense word alone ("migrated content") isn't a claim: it needs the company, a
        # number, or "has/was" to be saying what the company did.
        firm = _tokens(self.company.split(",")[0]).split()[:1]
        about_us = (re.search(r"\d", sentence) or re.search(r"\b(has|have|had|was|were)\b", sentence, re.I)
                    or (firm and f" {firm[0]} " in f" {_tokens(sentence)} "))
        if not about_us:
            return False
        # What the company WILL do is the approach, not a claim about the past.
        if re.search(r"\bwill\b", sentence, re.I) and not re.search(r"\b(has|have|had)\b", sentence, re.I):
            return False
        # Restating the solicitation isn't a claim; a sentence the library says almost word for
        # word is backed even without a citation.
        return not (_quoted(sentence, self.solicitation) or _quoted(sentence, self._library()))

    def style(self, text: str) -> str:
        # A [NEEDS: …] marker is a note to the reviewer by design; judge only the words around it.
        if _SELF_NOTE.search(re.sub(r"\[NEEDS:[^\]]*\]", "", text)) and not text.startswith("[CHECK:"):
            self.gaps.append(f'Reads like a note, not proposal text: "{text.strip()[:160]}"')
            return _flag(text)
        return text

    def filler(self, all_text: str) -> None:
        words = sorted({m.group(0).lower() for m in _FILLER.finditer(all_text)})
        if words:
            self.gaps.append("Replace these stock phrases with specifics: " + ", ".join(words))

    # ---- references and people ---------------------------------------------------------
    def reference(self, ref) -> None:
        hit = self.contracts.get(_norm(ref.contract_number))
        if not hit:
            self.gaps.append(f"Contract {ref.contract_number} isn't in your library, so it can't be cited.")
            ref._facts = [("Contract number", _needs(f"{ref.contract_number} is not in your library"))]
            for c in [ref.scope, *(w.work for w in ref.work), *ref.results]:
                c.text = _flag(c.text) if c.text.strip() else c.text
            return
        c, row = hit
        period = " to ".join(x for x in (c.get("start_date"), c.get("end_date")) if x)
        customer = c.get("customer") or ""
        if c.get("agency") and _norm(c["agency"]) not in _norm(customer):
            customer = f"{customer}, {c['agency']}" if customer else c["agency"]
        ref._facts = [
            ("Contract number", c.get("contract_number")),
            ("Contract title", c.get("title") or _needs("contract title")),
            ("Customer", customer or _needs("customer")),
            ("Point of contact", c.get("customer_poc") or _needs("customer point of contact: name, email, phone")),
            ("Role", c.get("role") or _needs("prime or subcontractor")),
            ("Total contract value", _money(c.get("value_usd")) or _needs("contract value")),
            ("Period of performance", period or _needs("period of performance")),
        ]
        if c.get("rating"):
            ref._facts.append(("CPARS rating", c["rating"]))
        if not c.get("customer_poc"):
            self.gaps.append(f"{c.get('contract_number')}: no customer point of contact in your library.")
        mine = {row["item_id"]}
        where = row.get("name") or "its file"
        others = [x for o in row.get("contracts") or [] if o is not c
                  for x in (o.get("title"), o.get("contract_number"))]
        part = self._part(row["item_id"], [c.get("title"), c.get("contract_number")], others)
        for x in [ref.scope, *(w.work for w in ref.work), *ref.results]:
            other = self._about_contract(x.source_id, c) if x.source_id not in mine else None
            if other:
                # A proposal or RFI response that writes this contract up is proof too, read only
                # in its part about this contract (so another contract's facts can't lend).
                self.cited(x, {x.source_id}, self._name(x.source_id), other)
            else:
                self.cited(x, mine, where, part)
        for w in ref.work:
            w.relevance = self.style(w.relevance)

    def _about_contract(self, item_id: str, c: dict) -> tuple[str, set[str]] | None:
        """The part of another library file about contract `c`, if that file names it (its number,
        any of its order numbers, its title, or the acronym in its title); otherwise None."""
        if not item_id or item_id not in self.rows:
            return None
        number = c.get("contract_number") or ""
        title = (c.get("title") or "").strip()
        anchors = _contract_anchors(number, title)
        raw = self.store.source_text(self.org, item_id) or ""
        low = raw.lower()
        starts = sorted(m.start() for a in anchors for m in re.finditer(_anchor_re(a), low))
        if not starts:
            return None
        # A past-performance book writes up several contracts, and names each in summaries too, so
        # take EVERY stretch that starts at a mention of this one and runs to the next mention of
        # another one (capped), never a single slice from the first mention.
        mine = {_norm(x) for x in re.split(r"[/,;\s]+", number) if x}
        others = [a for o in self.rows[item_id].get("contracts") or []
                  if not ({_norm(x) for x in re.split(r"[/,;\s]+", o.get("contract_number") or "") if x} & mine)
                  for a in _contract_anchors(o.get("contract_number") or "", (o.get("title") or "").strip())]
        stops = sorted(m.start() for a in others for m in re.finditer(_anchor_re(a), low))
        pieces, last = [], -1
        for st in starts:
            if st < last:
                continue
            end = next((x for x in stops if x > st + 20), len(raw))
            end = min(end, st + 8000)
            begin = max(0, low.rfind("\n", 0, max(0, st - 1)))
            pieces.append(raw[begin:end])
            last = end
        part = " ".join(pieces)
        return _tokens(part), _numbers(part)

    def person(self, p) -> None:
        hit = self.people.get(_norm(p.name)) or next(
            (v for k, v in self.people.items() if _norm(p.name) and (_norm(p.name) in k or k in _norm(p.name))),
            None)
        if not hit:
            self.gaps.append(f"{p.name} isn't in your resumes, so they can't be proposed.")
            p._facts = [("Name", _needs(f"{p.name} is not in your library"))]
            return
        rec, row = hit
        own = self._text(row["item_id"])
        # Their employer is stated only if the resume itself names the company.
        employer = self.company if _norm(self.company.split(",")[0]) and \
            f" {_tokens(self.company.split(',')[0])} " in f" {own} " else _needs("current employer")
        years = rec.get("years_experience")
        p._facts = [
            ("Name", rec.get("name")),
            ("Company", employer),
            ("Proposed position", p.proposed_position or _needs("proposed position")),
            ("Security clearance", rec.get("clearance") or _needs("security clearance")),
            ("Years of experience", f"{years:g}" if isinstance(years, (int, float)) else _needs("years of experience")),
            ("Current position", rec.get("current_role") or _needs("current position")),
            ("Level of effort", _needs("level of effort on this contract")),
            ("Education", rec.get("education") or _needs("education with year")),
            ("Certifications", ", ".join(rec.get("certifications") or []) or _needs("certifications")),
        ]
        mine = {row["item_id"]}
        where = row.get("name") or "their resume"
        others = [o.get("name") for o in row.get("people") or [] if o is not rec]
        part = self._part(row["item_id"], [rec.get("name")], others)
        for x in [*p.summary, *p.employment, *(q.experience for q in p.requirements)]:
            self.cited(x, mine, where, part)
        if not p.employment:
            self.gaps.append(f"{rec.get('name')}: no employment history with employers and dates in your library.")


def ground(doc, organization_id: str, employee_email: str | None, solicitation_text: str,
           company: str, notes: str = "") -> list[str]:
    """Apply all of the above to `doc` in place. Returns what a reviewer must look at."""
    from agent.proposal_writer import document_text

    g = _Grounder(organization_id, employee_email, solicitation_text, company, notes)
    for s in doc.sections:
        s.heading = g.style(s.heading)
        s.paragraphs = [g.prose(t) for t in s.paragraphs]
        s.bullets = [g.prose(t) for t in s.bullets]
        if s.table:
            s.table.rows = [[g.prose(c) for c in row] for row in s.table.rows]
        for r in s.references:
            g.reference(r)
        for p in s.people:
            g.person(p)
    doc.assumptions = [g.style(a) for a in doc.assumptions]
    g.filler(document_text(doc))
    g.gaps += [f"Approve before sending — promise not in any source: {c}" for c in doc.commitments]
    g.gaps += [f"Ask the contracting officer: {q}" for q in doc.questions]
    return g.gaps
