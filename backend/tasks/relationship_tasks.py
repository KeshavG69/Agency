"""The relationship engine's nightly sweep — who to reach out to, and the drafted note.

For one employee's network this does what the opportunity flow never does: finds the warm
relationships going quiet and the live personal hooks, drafts the outreach, and — only if it
passes the proof gate — persists a nudge the rep approves with one tap on the Today view.

THE SHAPE, and why each stage is where it is:

  1. SELECT (arithmetic, no model). Cadence over the graph's corr_count/last_contact picks the
     going-cold set; contacts with a recent personal fact add the hook set. Cheap, deterministic,
     scales to thousands of contacts — the reason there is no LLM here (see utils/cadence.py).
  2. DRAFT (the model, once per selected contact). The Relationship agent writes the note from
     the personal facts we actually hold — and only those.
  3. GATE (arithmetic again). utils/nudge_proof verifies the draft — recipient, grounding, no
     foreign address, length — and DROPS it if any check fails. The model is not trusted to
     police itself; this is what makes one-tap approve safe.
  4. PERSIST. A passing draft is upserted as a nudge; the action planner turns open nudges into
     Today cards. A human's later approve/dismiss is final (see client/nudge_store.py).

Fail-soft throughout: one contact whose draft errors or fails the gate is skipped, never fatal
to the sweep. Bounded per run by PER_EMPLOYEE_CAP so a day's nudges stay a short list.

Spec: docs/relationship-engine.md
"""
from __future__ import annotations

import logging

from app.worker import celery_app
from models.action import dedupe_key

logger = logging.getLogger(__name__)

# The most nudges one employee gets in a single sweep. Today is a work plan, not a wall —
# the warmest, freshest-lapsed relationships come first, the rest wait for another day.
PER_EMPLOYEE_CAP = 8

# Domains where a PERSONAL approach is not appropriate, however good the relationship.
# A contracting officer or program manager is bound by federal gift and procurement-integrity
# rules; "up for golf this weekend?" to a .gov/.mil address is at best awkward and at worst a
# complaint during a live source selection. Professional re-engagement to these contacts is
# ordinary BD and still allowed — only the personal-hook variant is withheld.
# (Suppressing even that while a pursuit is in source selection needs pursuit linkage on the
# contact; see docs/relationship-engine.md.)
GOVERNMENT_TLDS = (".gov", ".mil")


def _is_government(email: str) -> bool:
    """True for a federal/DoD address — including subdomains like us.army.mil."""
    domain = (email or "").strip().lower().rpartition("@")[2]
    return domain.endswith(GOVERNMENT_TLDS)


def _cycle_token(last_contact) -> str:
    """The silence this nudge is about, as a stable token for the dedupe key.

    Using the contact's `last_contact` day means: while the relationship stays silent the key
    is unchanged, so a contact the rep already dismissed is not re-offered for the SAME
    silence. The moment the relationship actually moves — they reply, or the rep's own sent
    mail lands — `last_contact` advances; if they then go quiet again that is a new cycle,
    a new key, and a legitimately new nudge. A static key (what this used to be) silenced
    every contact permanently after one decision.
    """
    if not last_contact:
        return "none"
    return str(last_contact)[:10]  # ISO timestamp -> YYYY-MM-DD


def _fact_values(personal: dict) -> dict[str, list[str]]:
    """`facts_store.personal_facts` returns {field: [{id, value, decided_by, ...}, ...]} — the
    shape the contact UI needs so it can remove a note. Everything downstream here (the
    prompt's facts block, the proof gate's allowlist, the card's hook) wants plain strings.

    Converting at this ONE boundary is deliberate: passing the raw rows through made the gate
    call `.strip()` on a dict and take down the whole employee's sweep on the first contact
    that had a personal fact.
    """
    out: dict[str, list[str]] = {}
    for field, rows in (personal or {}).items():
        values = [
            (r.get("value") if isinstance(r, dict) else r) for r in (rows or [])
        ]
        values = [str(v).strip() for v in values if v and str(v).strip()]
        if values:
            out[field] = values
    return out


def _sender_name(employee_email: str) -> str:
    """The rep's real display name, for the sign-off. Empty string when unknown — the sweep
    then skips that rep rather than manufacturing drafts the proof gate must reject.
    Delegates to the shared lookup the outreach and reply agents use too."""
    try:
        from utils.organizations import user_display_name

        return user_display_name(employee_email)
    except Exception as exc:  # noqa: BLE001 — no name is a recoverable state, not a failure
        logger.warning("Relationship sweep: could not read sender name for %s: %s",
                       employee_email, exc)
        return ""


def _relationship_line(
    corr_count: int, days_since, overdue_by, last_subject: str | None = None
) -> str:
    """The machine-assembled 'why now', mirroring the Mail agent's warmth phrasing. Assembled
    from the numbers, never written by the model. The last thread's subject is included so the
    draft can pick up where the two of them left off rather than opening cold."""
    n = int(corr_count or 0)
    warmth = "a warm, frequent contact" if n >= 10 else "a developing contact" if n >= 3 else "a light contact"
    seen = f", quiet {days_since} days" if days_since is not None else ""
    subject = (last_subject or "").strip()
    thread = f'; your last exchange was about "{subject}"' if subject else ""
    return f"corresponded {n}x ({warmth}){seen}{thread}"


def _reason(
    kind: str, corr_count: int, days_since, overdue_by, hook: str | None,
    last_subject: str | None = None, last_contact=None,
) -> str:
    """The evidence line the Today card shows — assembled from real signals, never written by
    the model (a model narrating its own reason could narrate one that isn't true).

    The LAST EXCHANGE leads when we have it. "Quiet 47 days" is a metric; "you last spoke
    about the GITSS-A recompete on 3 Aug" is a reason, and it is the thing a rep checks
    before deciding whether this message is worth sending at all.
    """
    n = int(corr_count or 0)
    bits = [f"warm ({n} emails)" if n >= 10 else f"{n} emails"]
    if days_since is not None:
        bits.append(f"quiet {days_since} days")
    subject = (last_subject or "").strip()
    if subject:
        day = str(last_contact)[:10] if last_contact else ""
        bits.append(f'last spoke about "{subject}"' + (f" on {day}" if day else ""))
    if kind == "relationship_personal" and hook:
        bits.append(f"you noted: {hook}")
    return "; ".join(bits) + "."


@celery_app.task(bind=True, name="relationship.for_employee", max_retries=1, default_retry_delay=60)
def sweep_relationships_for_employee(self, employee_email: str, organization_id: str) -> dict:
    """Produce this employee's relationship nudges. See module docstring for the four stages."""
    from client.crm_store import get_crm_store
    from client.facts_store import get_facts_store
    from client.graph_store import list_relationship_signals
    from client.nudge_store import get_nudge_store
    from agent.relationship_agent import draft_relationship_note
    from utils.cadence import get_org_cadence, select_going_cold
    from utils.nudge_proof import verify_nudge

    owner = (employee_email or "").strip().lower()
    org = (organization_id or "").strip()
    if not owner or not org:
        return {"nudges": 0, "reason": "missing owner or organization"}

    facts = get_facts_store()
    nudges = get_nudge_store()

    # 1. SELECT ------------------------------------------------------------------------
    signals = list_relationship_signals(owner, org)
    by_email = {s["email"]: s for s in signals if s.get("email")}

    # Going-cold: every warm contact overdue for a touch. NOT capped here — the cap is
    # applied AFTER the already-handled ones are removed (see below), because capping first
    # meant the same 8 warmest contacts were selected every night; once the rep had decided
    # all 8, every later sweep re-picked the same 8, skipped them as decided, and drafted
    # nothing for contacts 9..N. The engine went quiet after roughly one pass — the same
    # symptom as the dedupe bug, reached by a different route.
    cadence = get_org_cadence(org)
    cold = select_going_cold(signals, config=cadence)

    # Personal hooks: a contact we hold personal facts on gets the personal variant, because
    # a real hook is a better opener than "it has been a while". Government contacts never
    # do, whatever we know about them (see GOVERNMENT_TLDS).
    selected: list[tuple[str, dict, dict]] = []  # (kind, signal_row, personal_facts)
    gov_personal_withheld = 0
    for row in cold:
        pf = _fact_values(facts.personal_facts(org, row["email"]))
        if pf and _is_government(row["email"]):
            gov_personal_withheld += 1
            selected.append(("relationship_touch", row, {}))
        elif pf:
            selected.append(("relationship_personal", row, pf))
        else:
            selected.append(("relationship_touch", row, {}))

    sender = _sender_name(owner)
    if not sender:
        # Without a name the agent can only sign with a placeholder, which the proof gate
        # then rejects — every single draft. Failing loudly here beats a silent 100% gate-out
        # that looks like "the feature does nothing".
        logger.warning(
            "Relationship sweep: no display name for %s — skipping. Set firstName/lastName "
            "on the user so drafts can be signed.", owner,
        )
        return {"network": len(by_email), "selected": 0, "nudges": 0,
                "reason": "no sender name on the user record"}
    # Our own company name is legitimately nameable in a draft, so it joins the allowlist.
    try:
        from agent.company_profile import company_context

        company_name = (company_context(org) or ("", ""))[0] or ""
    except Exception:  # noqa: BLE001 — an unknown company name only tightens the gate
        company_name = ""

    # What we already hold for this employee — read ONCE, so the loop below never pays a
    # model call for a nudge that already exists (open: keep the draft the rep may have read;
    # decided: never re-offer).
    known_keys = nudges.existing_keys(org, owner)

    drafted = gated_out = refreshed = skipped_decided = 0
    gate_failures: list[str] = []
    # Keys this run considers live. Anything open that is NOT here has had its cycle end —
    # the contact replied, lapsed, or their facts changed — and must be retired rather than
    # left on Today saying "quiet 95 days" about someone who wrote yesterday.
    live_keys: set[str] = set()
    to_draft: list[tuple[str, dict, dict, str]] = []

    # Pass A — classify everything against what we already hold, WITHOUT paying for a model.
    for kind, row, pf in selected:
        key = dedupe_key(
            org, kind, None,
            f"{owner}:{row['email']}:{_cycle_token(row.get('last_contact'))}",
        )
        live_keys.add(key)
        existing_row = known_keys.get(key) or {}
        existing = existing_row.get("status")
        if existing is not None:
            if existing == "open":
                # Reuse the hook the DRAFT actually used (stored on the row), not "the first
                # fact we hold": if the model leaned on the second fact, recomputing here made
                # day-2's card say "you noted: cycling" above a message about golf.
                reason = _reason(kind, row.get("corr_count"), row.get("days_since"),
                                 row.get("overdue_by"), existing_row.get("hook"),
                                 row.get("last_subject"), row.get("last_contact"))
                # Same nudge, one day older: refresh the numbers, keep the message.
                nudges.refresh_signals(key, {
                    "corr_count": int(row.get("corr_count") or 0),
                    "days_since": row.get("days_since"),
                    "overdue_by": row.get("overdue_by"),
                    "reason": reason,
                    "last_subject": row.get("last_subject"),
                    "last_contact": (str(row.get("last_contact"))[:10]
                                     if row.get("last_contact") else None),
                })
                refreshed += 1
            else:
                skipped_decided += 1
            continue
        to_draft.append((kind, row, pf, key))

    # Pass B — only NEW nudges cost a model call, and only now is the cap applied, so a day's
    # budget is spent on contacts the rep has not already dealt with.
    for kind, row, pf, key in to_draft[:PER_EMPLOYEE_CAP]:
        contact = {
            "name": row.get("name"), "email": row["email"],
            "company": row.get("company"), "title": row.get("title"),
        }
        rel_line = _relationship_line(
            row.get("corr_count"), row.get("days_since"), row.get("overdue_by"),
            row.get("last_subject"),
        )

        # 2. DRAFT + 3. GATE -----------------------------------------------------------
        # Both inside one guard: a single contact must never be able to end the sweep, and
        # the gate is as capable of raising on odd input as the model is.
        try:
            draft = draft_relationship_note(
                kind, contact, rel_line, pf, organization_id=org, sender_name=sender,
            )
            known = [v for values in pf.values() for v in values]
            # Everything the draft may legitimately name: who it is to, where they work, who
            # is signing, our own company, and what the two of them last talked about.
            # Anything capitalised in the body not accountable to this is an invented specific.
            proof = verify_nudge(
                kind=kind, recipient_email=row["email"], subject=draft.subject,
                body=draft.body, drew_on=draft.drew_on, known_facts=known,
                context=[
                    row.get("name") or "", row.get("company") or "", row.get("title") or "",
                    row.get("last_subject") or "", sender, company_name,
                ],
            )
        except Exception as exc:  # noqa: BLE001 — one bad contact never sinks the sweep
            logger.warning("Relationship draft/gate failed for %s -> %s: %s",
                           owner, row["email"], exc, exc_info=True)
            continue

        if not proof.ok:
            gated_out += 1
            failed = ", ".join(c.name for c in proof.failures)
            gate_failures.extend(c.name for c in proof.failures)
            # WARNING, not INFO: a gated-out draft means the rep silently gets no nudge for
            # this contact. If that starts happening often it has to be visible in the log,
            # not discovered by a rep asking why the feature does nothing.
            logger.warning("Relationship nudge gated out for %s -> %s (%s)\n%s",
                           owner, row["email"], failed, proof.summary())
            continue

        # 4. PERSIST -------------------------------------------------------------------
        hook = draft.drew_on[0] if draft.drew_on else None
        nudges.upsert({
            # Cycle token = the silence being addressed, so a contact becomes nudge-able
            # again once the relationship actually moves (see _cycle_token).
            "dedupe_key": key,
            "organization_id": org,
            "owner_email": owner,
            "kind": kind,
            "contact_email": row["email"],
            "contact_name": row.get("name"),
            "contact_company": row.get("company"),
            "subject": draft.subject,
            "body": draft.body,
            # Recomputed with the hook the draft ACTUALLY used, which may differ from the
            # preview above when the model leaned on a different stored fact.
            "reason": _reason(kind, row.get("corr_count"), row.get("days_since"),
                              row.get("overdue_by"), hook,
                              row.get("last_subject"), row.get("last_contact")),
            "last_subject": row.get("last_subject"),
            "last_contact": str(row.get("last_contact"))[:10] if row.get("last_contact") else None,
            "drew_on": draft.drew_on,
            # Stored so a later refresh can rebuild the reason with the SAME hook the message
            # was written around.
            "hook": hook,
            "corr_count": int(row.get("corr_count") or 0),
            "days_since": row.get("days_since"),
            "overdue_by": row.get("overdue_by"),
        })
        drafted += 1

    # Retire open nudges whose cycle has ended. Without this an "it has been a while" card
    # outlives the silence it describes: the contact replies, `last_contact` advances, the old
    # key stops being selected — but the row stays open, the planner keeps re-emitting its
    # card, and one tap sends "been a while" to someone who wrote yesterday.
    stale_ids = [
        meta["id"] for key, meta in known_keys.items()
        if meta.get("status") == "open" and key not in live_keys
    ]
    expired = nudges.close_open_by_ref(org, stale_ids) if stale_ids else 0
    if expired:
        # Their Today cards must go too, or the card outlives the nudge behind it.
        crm = get_crm_store()
        for nid in stale_ids:
            try:
                crm.close_action_by_ref(org, nid, status="expired")
            except Exception as exc:  # noqa: BLE001 — the nudge is already retired
                logger.warning("Relationship sweep: could not close card for %s: %s", nid, exc)

    logger.info(
        "Relationship sweep for %s (cadence %dd/%dd, lapse %dd): %d in network, %d selected, %d new nudges, %d refreshed, "
        "%d already decided, %d gated out (%s), %d expired, %d gov contacts kept professional",
        owner, cadence.warm_days, cadence.developing_days, cadence.lapsed_after_days,
        len(by_email), len(selected), drafted, refreshed, skipped_decided,
        gated_out, ", ".join(sorted(set(gate_failures))) or "-", expired,
        gov_personal_withheld,
    )
    return {"network": len(by_email), "selected": len(selected),
            "nudges": drafted, "refreshed": refreshed,
            "skipped_decided": skipped_decided, "gated_out": gated_out,
            "gate_failures": sorted(set(gate_failures)), "expired": expired,
            "gov_personal_withheld": gov_personal_withheld}


@celery_app.task(name="relationship.daily")
def sweep_all_relationships() -> dict:
    """Beat entry — a relationship sweep for every employee with a mailbox. Runs before the
    action plan so the morning's nudges land on the same day's Today list."""
    from utils.organizations import iter_active_memberships

    # Iterate active memberships, not a non-existent users.organization_id field — that query
    # matched nobody, so this beat job dispatched zero sweeps (see iter_active_memberships).
    dispatched = 0
    for email, org in iter_active_memberships():
        sweep_relationships_for_employee.delay(email, org)
        dispatched += 1
    logger.info("Relationship sweep: dispatched %d employee sweeps", dispatched)
    return {"dispatched": dispatched}
