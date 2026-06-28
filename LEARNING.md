# Collecct — Understanding Checklist

Tracks what you've demonstrably mastered. `[ ]` = not yet, `[x]` = confirmed.

## Stage 1 — The problem & motivation (the WHY)
- [ ] What Collecct is, in one sentence, and who it's for
- [ ] The problem it solves (and why that problem exists in govcon)
- [ ] The branches we considered (build vs buy; engine; EspoCRM vs Mongo)

## Stage 2 — The govcon domain (what we're modeling)
- [ ] The 5 phases: Discovery → Capture → Solution → Proposal → Submit/Learn
- [ ] Pre-RFP vs post-RFP (the dividing line + the "quiet period")
- [ ] How proposals are scored (technical/PWS, oral+sample task, resumes, past perf, admin)
- [ ] Key personnel & resume tailoring (why Phase 4, needs the PWS)
- [ ] Human approval gates (0–6)

## Stage 3 — The architecture
- [ ] The stack: MongoDB, FastAPI, Agno, Celery, Composio, iDrive
- [ ] Why Agno (engine choice)
- [ ] Why MongoDB over EspoCRM (the reversal + the reason)
- [ ] Data model: opportunities, calls, tasks, documents (+ companies/people planned)
- [ ] Why the Mongo switch was cheap (store-agnostic pipeline)

## Stage 4 — The agents
- [ ] Analyst Agent: input, what it judges, structured output, parallel fan-out
- [ ] Pattern: agentic-read vs code-feeds; deterministic write
- [ ] CRM/Relation Agent: the knowledge-graph idea (5000 → 10)
- [ ] Capture agent (merged capture-plan + shaping) → Mail
- [ ] Doc generation (python_repl + skills + iDrive) + SharePoint export

## Stage 5 — Key design decisions & edge cases
- [ ] Dedup (unique partial index) + the `analyzed_at` marker
- [ ] Incremental sync (`last_synced`) — catching async replies
- [ ] Human-in-the-loop: draft-only mail, gates, user-triggered SharePoint
- [ ] Quiet period (stop outreach after the RFP)
- [ ] Resumes: customer stakeholders (none) vs key personnel (Phase 4)
- [ ] LinkedIn: email-first, CSV enrichment, no scraping (and why)

## Stage 6 — Broader context
- [ ] Why it matters: differentiation vs CLEATUS/GovDash (the network moat)
- [ ] Where PriceIQ fits
- [ ] Built vs planned (current status)
