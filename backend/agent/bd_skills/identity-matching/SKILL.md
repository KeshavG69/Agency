---
name: identity-matching
description: Use when deciding who the person behind an email address actually is, and when to refuse. Read this before attributing a name, title, or profile to a contact whose identity is not already established.
---

# Identity matching

You are given an email address and a company. You need the person. Getting this wrong
writes a stranger's career onto a customer's record, so the procedure fails closed.

## Why the obvious approach does not work

`pmarchetti@fernhill.com` is not a name. Searching for the address directly returns
nothing. Asking a model what the local part stands for produces "Paula Marchetti" —
which happens to be right, and would have been just as confident had it been wrong. You
cannot tell the difference afterwards. That is why guessing is banned outright.

What works is decomposition: `pmarchetti` contains the surname `marchetti`, and
searching *that* alongside the company returns the profile as the first result. The
guess went into the **query**; the answer came from the page.

> **Guess where to look. Never guess what you will find.**

## The procedure

0. **Read what we already have, first.** It is free and it is often decisive. If they
   have ever replied to us from that address, you already hold the strongest evidence
   available anywhere — and their signature block may hand you the job title too.
   Start here, not at a search engine.
1. **Decompose the address** into candidate surnames and initials. These are leads for
   the search box, not answers.
2. **Search the surname with the employer.** Take the profile the search returns.
3. **Check two things, not one:**
   - the employer on the profile matches the company we hold, and
   - the real name is consistent with the email local part (`p` + `marchetti`).
4. **Both, or it is not them.** One of the two is not a weaker match — it is a
   different person who happens to share something.
5. If no candidate passes, **stop**. Leaving `Pmarchetti` on the record is the correct
   outcome when you do not know.

## Things that look like evidence and are not

- **A search result.** Search tells you where to look. A query for a common name will
  return three different people, all with total confidence.
- **A matching first name.** Half the Chrises at a company are not your Chris. The
  surname or the employer has to carry it.
- **A very plausible expansion.** `jsmith` is probably J. Smith. Probably is not a
  source.
- **A third-party aggregator's view of somebody's job title.** It pools stale records.
  For identity, the person's own profile or their own signature wins.

## Reporting the match

| What you have | Evidence to record | What happens |
| --- | --- | --- |
| Employer *and* name both match | `linkedin.employer_name` | Written to the record. |
| They replied from that address | `outlook.thread-reply` | Written to the record. |
| Their signature states it | `outlook.signature-block` | Written to the record. |
| Only one of the two checks passes | `employer-only` | Offered to a rep as a suggestion. |
| Sources disagree | add a `contradiction` entry | Held. Nobody is shown a guess. |

That fourth row is what this exists for. Four people share a surname at one company; a
human settles it in three seconds. A suggestion is not a failed match — it is the
match, handed to the one person who can finish it.

Do not add evidence you did not observe in order to push a claim over the line.

## When the person is genuinely not findable

Some people have no profile, or one with no employer, or a name that cannot be
reconciled with their address. Say so and move on. A contact that keeps its placeholder
name is one a rep fixes in five seconds; a contact carrying the wrong person's job
history is one nobody knows to fix.
