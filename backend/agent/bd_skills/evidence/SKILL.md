---
name: evidence
description: Use when recording a fact about a contact or a company — picking the right evidence kind for what you actually saw, and understanding why a claim was written to the record, offered as a suggestion, or held. Read this before calling any tool that records a fact.
---

# Evidence

You never set a confidence score. You report **what you saw**, and a ledger prices it.

Getting the `kind` right is therefore the whole job — it is the difference between a
fact landing on a contact's record and a rep being asked a question. This is
deliberate: models grade their own certainty badly, and inflate it to appear useful.
The arithmetic is not yours to do.

## The kinds

**Primary — these identify the subject, so one of them can carry a fact alone.**

| Kind | Use it when |
| --- | --- |
| `samgov.entity-record` | The company's own SAM.gov registration states it. |
| `sam.poc-listed` | They are the named point of contact on a SAM.gov notice. |
| `outlook.thread-reply` | They replied to us, from that address, on a thread we hold. Proof of identity. |
| `gov-domain-rule` | A `.gov`/`.mil` domain identifies the agency. A lookup, not a guess. |
| `outlook.signature-block` | Their own signature states it. The best source there is for a job title — people update a signature the week they are promoted. |
| `pdl.domain-company` | The email domain matched our company dataset. |
| `company.own-website` | The company's own site describes its business. |
| `sharepoint.authored-doc` | They authored a document in our tenant. |
| `outlook.meeting-attend` | They accepted a meeting on our calendar. |

**Supporting — true, but consistent with many people, so never enough alone.**

| Kind | Use it when |
| --- | --- |
| `web.cited-claim` | A third-party page states it. **Requires a source URL.** |
| `outlook.address-book` | It is saved on their Outlook contact card. |
| `handle.name-form` | The address is a construction of their name. Weak: `jsmith@` is a form of every J. Smith's name. |
| `domain-derived-name` | The company name was derived from the domain. Often right, occasionally embarrassing. |
| `employer-only` | The employer matches but the name does not. Nearly worthless alone — deliberately, because this is how a colleague gets filed as the contact. |

**`contradiction` — when two sources disagree.**

Record it. It does not lower the score a little; it **holds** the claim entirely, which
is correct. A signature saying one employer and a SAM.gov notice saying another is not
60% true. It is unresolved, and a rep should see it that way.

## One entry per independent source

Two facts read off the same page are **one** observation, not two. A company's about
page that gives both the industry and the location is one `company.own-website`, not
two. Splitting it would double-count a single page into false certainty, which is
exactly the arithmetic this system exists to prevent.

## Write `detail` for the human who will read it

It renders in a tooltip next to the value.

- Good: `their signature on 14 Jul reads "VP, Business Development"`
- Bad: `signature match confirmed`

## What happens next, so you can stop guessing about it

- Strong, with a primary source → **written to the record.**
- Otherwise → **stored as a suggestion** under the field, for a rep to settle.
- Too weak → kept out entirely.

A suggestion is a good outcome, and often the *correct* one. Do not go looking for
extra evidence to push a claim over the line — that is how a wrong answer gets dressed
up as a right one.
