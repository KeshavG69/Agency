"""Smoke test: the Mail Agent's parallel batch.

Feeds ONE opportunity + several recommended contacts (varied relationship
strength) and runs draft_outreach_batch — one agent per email, capped at 15
concurrent. Proves: (a) relationship-aware tone, (b) grounding, (c) parallelism.

Run:  uv run python scripts/test_mail.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.settings import settings  # noqa: E402
from agent.mail_agent import draft_outreach_batch  # noqa: E402

OPP = {
    "title": "C5ISR Network Modernization Engineering Support Services",
    "agency": "U.S. Army CECOM",
    "naics": "541512",
    "set_aside": "Women-Owned Small Business (WOSB)",
    "opp_type": "BAA",  # Broad Agency Announcement -> agent should lead with R&D angle
    "place_of_performance": "Aberdeen Proving Ground, MD",
    "description": "Engineering and integration support for tactical network modernization (WIN-T follow-on).",
}

# Mimics what the CRM agent recommends. Spread of relationship strength:
#   Kaitlin = developing (4x), Shin = lightly in touch (1x), Sameer = 1x,
#   brand-new = not in the graph at all.
CONTACTS = [
    {"name": "Kaitlin Kavana", "email": "kaitlin.kavana@deepgram.com",
     "company": "Deepgram", "title": "Sales Development Representative",
     "relevance_score": 0.7, "reason": "Speech/AI vendor — possible teaming on signal processing."},
    {"name": "Shin Kim", "email": "shin@eraser.io", "company": "Eraser",
     "title": "Founder", "relevance_score": 0.6,
     "reason": "Founder contact; diagramming/architecture tooling."},
    {"name": "Mohammed Sameer", "email": "sameer@jamaru.ai", "company": "Jamaru",
     "relevance_score": 0.5, "reason": "Early contact in the AI space."},
    {"name": "Dana Prime", "email": "dana.prime@primesys.com", "company": "PrimeSys",
     "title": "Capture Manager", "relevance_score": 0.8,
     "reason": "Large prime that often subs C5ISR engineering — never emailed before."},
]


def _line(s: str) -> None:
    print(s)


def main() -> None:
    _line(f"model        = {settings.OPENROUTER_BASE_URL} (mail agent default: claude-sonnet-4.6)")
    _line(f"opportunity  = {OPP['title']}  [{OPP['opp_type']}]")
    _line(f"contacts     = {len(CONTACTS)}  (semaphore limit = 15 -> all run at once)\n")

    t0 = time.time()
    drafts = draft_outreach_batch(
        OPP, CONTACTS, limit=15
    )
    dt = time.time() - t0

    ok = sum(1 for d in drafts if d is not None)
    _line("=" * 78)
    _line(f"BATCH DONE in {dt:.1f}s — {ok}/{len(CONTACTS)} drafts produced")
    _line("=" * 78)

    import json

    for contact, draft in zip(CONTACTS, drafts):
        _line("\n" + "=" * 78)
        _line(f"CONTACT: {contact['name']} <{contact['email']}>  ·  {contact.get('company','')}")
        if draft is None:
            _line("  (draft failed — see logs)")
            continue
        _line("\n--- structured MailDraft (what the frontend renders) ---")
        _line(json.dumps(draft.model_dump(), indent=2, ensure_ascii=False))
        _line("\n--- OUTLOOK_SEND_EMAIL args (what the send tool receives) ---")
        _line(json.dumps(draft.outlook_send_args(user_id="me"), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
