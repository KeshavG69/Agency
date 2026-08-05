"""The tick — the third way work starts in Collecct.

Until now there were two: the daily clock, and a human clicking a button. Neither can
express "come back to this in two weeks", which is why the things the system already
notices get dropped. This module leases whatever is DUE on the agent_tasks list and runs
it. It makes no decisions of its own: the decision was made when the row was written.

TWO LANES, ON PURPOSE
  * direct  — mechanical work, no model: fast, cheap, 60 at a time, 2-minute lease.
  * llm     — needs judgement: slow and costly, 12 at a time, 30-minute lease.
Run separately so a mechanical job never sits in a queue behind a five-minute research
run. This mirrors the split in trycompai/crm, where logos and photos never touch a model.

FAILURE IS EXPECTED AND CHEAP
A handler that finds nothing calls `stand_down` — the row is pushed a month out rather
than finished, because the answer rarely changes and the lookup costs money every time it
is asked. A handler that crashes simply lets its lease expire and the row is retried, up
to MAX_ATTEMPTS, after which `retire_exhausted` closes it with a readable reason.

Scheduled from app/worker.py:  "agent-task-tick" -> crontab(minute="*")
See docs/enrichment-implementation-plan.md §5.7.
"""
import logging

from celery import group

from app.worker import celery_app
from client.events_store import record_event
from client.task_store import (
    BATCH_DIRECT,
    BATCH_LLM,
    DIRECT_KINDS,
    LEASE_DIRECT_MS,
    LEASE_LLM_MS,
    LLM_KINDS,
    STAND_DOWN_DAYS,
    get_task_store,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="agent_tasks.tick")
def tick() -> dict:
    """Lease everything due and fan it out. Runs every minute; usually does nothing."""
    store = get_task_store()

    retired = store.retire_exhausted()
    if retired:
        logger.info("agent_tasks: retired %d exhausted task(s)", len(retired))

    direct = store.claim_due(BATCH_DIRECT, DIRECT_KINDS, LEASE_DIRECT_MS)
    llm = store.claim_due(BATCH_LLM, LLM_KINDS, LEASE_LLM_MS)

    for batch in (direct, llm):
        if batch:
            group(run_agent_task.s(t) for t in batch).apply_async()

    if direct or llm:
        logger.info("agent_tasks tick: dispatched %d direct, %d llm", len(direct), len(llm))
    return {"direct": len(direct), "llm": len(llm), "retired": len(retired)}


@celery_app.task(bind=True, name="agent_tasks.run", max_retries=0)
def run_agent_task(self, task: dict) -> dict:
    """Run one leased row. No retry here: the LEASE is the retry mechanism — if this dies,
    the lease expires and the tick picks the row up again with its attempt count intact."""
    kind = task.get("kind")
    handler = _HANDLERS.get(kind)
    if handler is None:
        get_task_store().complete_task(task["id"], f"No handler for '{kind}'.")
        logger.warning("agent_tasks: no handler for kind %r", kind)
        return {"id": task.get("id"), "handled": False}
    return handler(task)


# --- handlers ----------------------------------------------------------------------


def _research_company(task: dict) -> dict:
    """Answer the question `company_needs_research` has been asking since day one."""
    from agent.company_research_agent import research_company
    from client.facts_store import get_facts_store
    from client.graph_store import update_company_for_domain

    store = get_task_store()
    org, domain = task["organization_id"], task["subject"]["id"]

    result = research_company(domain, budget=int(task.get("budget") or 3))

    if not result.found:
        # Not a failure. Ask again in a month, not tomorrow.
        store.stand_down(task["id"], STAND_DOWN_DAYS,
                         "nothing found on the public web; the answer rarely changes")
        record_event(org, "company_research", "company", domain,
                     "could not establish what this company is",
                     f"No usable public source for {domain}; will look again in "
                     f"{STAND_DOWN_DAYS} days.", ok=False)
        logger.info("Company research: nothing found for %s (standing down)", domain)
        return {"id": task["id"], "domain": domain, "found": False}

    # WHICH kind of source this was is decided here, in code, by comparing hosts — never
    # by asking the model how good its own source is. The company's own site is
    # authoritative for what the company does; a third-party page only corroborates.
    own_site = _is_own_site(result.source_url, domain)
    evidence = [{
        "kind": "company.own-website" if own_site else "web.cited-claim",
        "detail": (result.description or result.industry or "").strip()[:300],
        "source_url": result.source_url,
    }]

    # Facts are per-contact, so apply the company's details to everyone on that domain —
    # one lookup, the whole org's network improved.
    facts = get_facts_store()
    written = 0
    for email in _emails_on_domain(org, domain):
        written += sum(
            1
            for outcome in facts.record_many(
                org, email,
                [("company", result.name), ("industry", result.industry),
                 ("website", result.website), ("linkedin", result.linkedin)],
                evidence,
            ).values()
            if outcome.stored
        )

    updated = 0
    try:
        updated = update_company_for_domain(
            org, domain, industry=result.industry,
            company_website=result.website, company_linkedin=result.linkedin,
        )
    except Exception as exc:  # noqa: BLE001 — the facts are already saved; the graph can lag
        logger.warning("Graph update after company research failed for %s: %s", domain, exc)

    outcome = f"{result.industry or 'researched'} — {(result.description or '')[:160]}".strip(" —")
    store.complete_task(task["id"], outcome)
    record_event(
        org, "company_research", "company", domain,
        f"researched {result.name or domain}", outcome,
        tool="web_search",
    )
    logger.info(
        "Company research: %s -> %r (%d contacts updated, %d facts)",
        domain, result.industry, updated, written,
    )
    return {"id": task["id"], "domain": domain, "found": True,
            "contacts": updated, "facts": written}


def _recheck_opportunity(task: dict) -> dict:
    """Re-judge an opportunity the Analyst asked to look at again."""
    # Lazy import: analyst_tasks imports from this package's siblings at module level.
    from client.crm_store import get_crm_store
    from tasks.analyst_tasks import analyze_opportunity_task

    store = get_task_store()
    org, opp_id = task["organization_id"], task["subject"]["id"]
    opp = get_crm_store().get_opportunity(opp_id, org)
    if not opp:
        store.complete_task(task["id"], "The opportunity this names is gone.")
        return {"id": task["id"], "rechecked": False}

    analyze_opportunity_task.delay(opp)
    store.complete_task(task["id"], f"Re-analysis queued: {task.get('reason') or 'scheduled recheck'}")
    return {"id": task["id"], "rechecked": True, "opportunity_id": opp_id}


# Suffixes where the registrable name is the THIRD label from the right, not the second.
# A short list, not the full Public Suffix List: govcon contacts are overwhelmingly .com /
# .gov / .mil / .org, and pulling in a PSL dependency to serve a handful of edge cases is a
# poor trade. An unlisted multi-part suffix simply falls back to the stricter comparison,
# which errs toward "not their own site" — the safe direction.
_MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "com.au", "net.au", "org.au",
    "co.nz", "co.za", "com.br", "com.mx", "co.in", "com.sg",
})


def _registrable(host: str) -> str:
    """The company-owned part of a hostname: aai.textron.com -> textron.com.

    Approximate by design (see _MULTI_SUFFIXES). Returns the host unchanged when it is
    already short enough to be registrable.
    """
    labels = [x for x in (host or "").split(".") if x]
    if len(labels) < 3:
        return ".".join(labels)
    return ".".join(labels[-3:] if ".".join(labels[-2:]) in _MULTI_SUFFIXES else labels[-2:])


def _is_own_site(source_url: str | None, domain: str) -> bool:
    """True when the page read was published by the company that owns `domain`.

    Deterministic on purpose. Asking a model "was this an authoritative source?" invites
    exactly the self-grading the evidence model exists to remove; comparing two hostnames
    is something code can simply know.

    Matches on the REGISTRABLE domain, so researching `aai.textron.com` and finding the
    answer on `textron.com` counts — a subsidiary's page on its parent's site is still the
    company describing itself, and the strict host-equality version scored that as a
    third-party claim.

    It will NOT match `textronsystems.com`, and that is correct: knowing AAI belongs to
    Textron Systems is a fact about corporate structure, not about DNS. A brand-adjacent
    domain stays a third-party source, which downgrades the claim to a suggestion rather
    than asserting it — the right failure direction.
    """
    from urllib.parse import urlparse

    host = urlparse((source_url or "").strip()).netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    dom = (domain or "").strip().lower()
    if not host or not dom:
        return False
    # Exact / subdomain match first, then the registrable-domain relaxation. Comparing the
    # registrable forms is also what blocks the lookalike attack: a source at
    # `nexagen.com.evil.example` registers as `evil.example`, not `nexagen.com`.
    if host == dom or host.endswith("." + dom):
        return True
    return _registrable(host) == _registrable(dom)


def _emails_on_domain(organization_id: str, domain: str) -> list[str]:
    """Every contact we hold on this domain, across all owners in the org."""
    from client.graph_store import get_graph

    try:
        res = get_graph(organization_id).ro_query(
            "MATCH (p:Person) WHERE p.domain = $dom RETURN DISTINCT p.email",
            params={"dom": (domain or "").strip().lower()},
        )
        return [r[0] for r in res.result_set if r[0]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Listing contacts on %s failed: %s", domain, exc)
        return []


_HANDLERS = {
    "research_company": _research_company,
    "recheck_opportunity": _recheck_opportunity,
}
