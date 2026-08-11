"""Central MongoDB index definitions — the single source of truth for every index.

`ensure_indexes()` is idempotent (create_index is a no-op when the index already exists),
so it runs on every app startup (app/server.py). Every index here is chosen to match how a
collection is ACTUALLY queried — derived from an audit of every find / find_one / sort /
count / aggregate / distinct / upsert across client/, routers/, tasks/ and auth/ (see the
comment on each entry). Some are ALSO created in a store's __init__ as a safety net for
worker-only processes that never call ensure_indexes; listing them here too keeps the whole
index surface visible in one place. The overlap is harmless (idempotent).

Run it standalone to apply everything (and drop the known-redundant indexes) without booting
the app:

    python -m utils.db_indexes        # from the backend/ dir

WHAT'S NOT INDEXED, ON PURPOSE (documented so nobody "fixes" it with a useless index):
  * opportunities free-text search — case-insensitive unanchored $regex can't use a btree.
  * $facet / $group / distinct scans (status pill counts, queue_health, suggestion_contacts)
    — an aggregation branch can't use an index; the outer $match uses the (org, …) prefix.
  * agent_tasks claim_due's [priority -1, due_at 1] sort — an in-memory sort over the already
    narrowed "open & due & unleased" set; narrowing that set is what the index is for.
  * the beat tasks' find({}, {_id:1}) over all orgs, and the token/mailbox maintenance sweeps
    — deliberate full scans, run rarely.
"""
import logging

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# (collection, keys, options) — index names are auto-derived (stable + idempotent).
_INDEXES: list[tuple[str, list, dict]] = [
    # ---- auth ----------------------------------------------------------------------
    # users: find_one({email}) is the constant login / auth-dependency hit; the members
    # listing + role counts filter on organizations.$elemMatch(organization_id).
    ("users", [("email", ASCENDING)], {"unique": True}),
    ("users", [("organizations.organization_id", ASCENDING)], {}),
    # organizations: slug lookups during signup + slug-uniqueness generation loop.
    ("organizations", [("slug", ASCENDING)], {}),
    # invitations: accept/validate by token_hash; list + stats + dup-check by org.
    ("invitations", [("token_hash", ASCENDING)], {}),
    ("invitations", [("organization_id", ASCENDING)], {}),
    # refresh tokens: token_id on every refresh (unique); token_family on reuse-revoke;
    # user_email on logout-all. The last two used to full-scan the collection.
    ("refresh_tokens", [("token_id", ASCENDING)], {"unique": True}),
    ("refresh_tokens", [("token_family_id", ASCENDING)], {}),  # NEW — revoke_refresh_token_family
    ("refresh_tokens", [("user_email", ASCENDING)], {}),       # NEW — revoke_user_refresh_tokens (logout all)
    # email verification + password reset: validate/mark by token_hash (the link click);
    # create dup-check + resend rate-limit count by user_id (both used to scan).
    ("email_verifications", [("token_hash", ASCENDING)], {}),
    ("email_verifications", [("user_id", ASCENDING)], {}),     # NEW — dup-check + rate-limit
    ("password_resets", [("token_hash", ASCENDING)], {}),
    ("password_resets", [("user_id", ASCENDING)], {}),         # NEW — rate-limit + invalidate-pending
    # onboarding progress: EVERY get/update/upsert is find_one/update_one on
    # (user_id, organization_id) — the dashboard/onboarding load. Was UNINDEXED, so it did
    # a collection scan every time: the single most impactful missing index. Non-unique to
    # guarantee creation even if legacy duplicate (user_id, org) rows exist; enforce unique
    # after a dedupe check if you want the integrity guard too.
    ("onboarding_progress",
     [("user_id", ASCENDING), ("organization_id", ASCENDING)], {}),  # NEW

    # ---- opportunities / CRM -------------------------------------------------------
    # (org, sol#) unique + notice_id are also created in crm_store; listed for completeness.
    ("opportunities", [("organization_id", ASCENDING), ("solicitation_number", ASCENDING)],
     {"unique": True, "partialFilterExpression": {"solicitation_number": {"$type": "string"}}}),
    ("opportunities", [("notice_id", ASCENDING)], {}),
    # THE list page: filter by org, sort priority_score desc (with _id tiebreak); also the
    # RBAC visibility filter (org, assigned_to) and the (org, analyzed_at) unanalyzed batch.
    ("opportunities", [("organization_id", ASCENDING), ("priority_score", DESCENDING), ("_id", DESCENDING)], {}),
    ("opportunities", [("organization_id", ASCENDING), ("assigned_to", ASCENDING)], {}),
    ("opportunities", [("organization_id", ASCENDING), ("analyzed_at", ASCENDING)], {}),
    # calls / tasks / documents: fetched per opportunity for the detail pane.
    ("calls", [("opportunity_id", ASCENDING)], {}),
    ("tasks", [("opportunity_id", ASCENDING)], {}),
    ("documents", [("opportunity_id", ASCENDING), ("type", ASCENDING)], {}),
    # call briefs: one per (org, opportunity, contact, rep) — the upsert key. The dialog reads
    # by (org, opportunity, rep), which this index's prefix serves.
    ("call_briefs",
     [("organization_id", ASCENDING), ("opportunity_id", ASCENDING),
      ("employee_email", ASCENDING), ("contact_email", ASCENDING)],
     {"unique": True}),
    # outreach log: collision lookups by (org, contact_email).
    ("outreach_log", [("organization_id", ASCENDING), ("contact_email", ASCENDING)], {}),
    # mail triage: unique per (employee, message) so a re-delivered webhook never duplicates a
    # card; the list endpoint queries (employee, status) newest-first.
    ("mail_triage", [("employee_email", ASCENDING), ("message_id", ASCENDING)], {"unique": True}),
    ("mail_triage", [("employee_email", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)], {}),
    # mailbox sync bookmark: one row per mailbox; the lookup IS the uniqueness key.
    ("mailbox_sync", [("owner_email", ASCENDING), ("organization_id", ASCENDING)], {"unique": True}),
    # daily action plan. `dedupe_key` unique is the whole idempotency contract — the planner
    # runs daily AND after every pipeline event, and this is what stops it creating a second
    # "Approve capture on GITSS-A". The worklist index serves the ONE query the Today view
    # makes (org + who + open + due within the horizon); the opp index serves the planner's
    # auto-complete / expire sweeps, which are per-pursuit.
    ("actions", [("dedupe_key", ASCENDING)], {"unique": True}),
    ("actions", [("organization_id", ASCENDING), ("status", ASCENDING), ("due_on", ASCENDING),
                 ("assigned_to", ASCENDING)], {}),
    ("actions", [("organization_id", ASCENDING), ("opportunity_id", ASCENDING),
                 ("status", ASCENDING)], {}),

    # ---- agent infrastructure ------------------------------------------------------
    # contact facts: one row per distinct claim (unique); the per-contact read split by
    # status; and the org-wide review queue sorted by score desc (the score key keeps that
    # sort index-backed instead of an in-memory sort over every open suggestion).
    ("contact_facts",
     [("organization_id", ASCENDING), ("email", ASCENDING), ("field", ASCENDING), ("value", ASCENDING)],
     {"unique": True}),
    ("contact_facts",
     [("organization_id", ASCENDING), ("email", ASCENDING), ("status", ASCENDING)], {}),
    ("contact_facts",
     [("organization_id", ASCENDING), ("status", ASCENDING), ("score", DESCENDING)], {}),
    # agent tasks: the enqueue de-dup lookup, and the claim query (open + due + unleased).
    ("agent_tasks",
     [("organization_id", ASCENDING), ("kind", ASCENDING), ("subject.id", ASCENDING),
      ("finished_at", ASCENDING)], {}),
    ("agent_tasks",
     [("finished_at", ASCENDING), ("due_at", ASCENDING), ("lease_until", ASCENDING),
      ("priority", DESCENDING)], {}),
    # agent events: append-only trail, read oldest-first for one record.
    ("agent_events",
     [("organization_id", ASCENDING), ("subject.id", ASCENDING), ("created_at", ASCENDING)], {}),
]

# Indexes that an audit found are NEVER used and are safe to drop (not recreated by any store
# __init__). These collections are only ever queried by token_hash or user_id, never by email.
_REDUNDANT: list[tuple[str, list]] = [
    ("email_verifications", [("email", ASCENDING)]),
    ("password_resets", [("email", ASCENDING)]),
    ("invitations", [("email", ASCENDING)]),
    # SUPERSEDED, and actively harmful if left: briefs used to be one-per-(org, opp, rep).
    # They are now one per CONTACT, so this unique index would reject the second contact's
    # brief on the same pursuit. The 4-key index above replaces it.
    ("call_briefs",
     [("organization_id", ASCENDING), ("opportunity_id", ASCENDING), ("employee_email", ASCENDING)]),
]

# Known prefix-redundant indexes still created in crm_store.__init__ (opportunities:
# (organization_id) / (analyzed_at); documents: (opportunity_id)). They're covered by the
# compound indexes above and could be removed from crm_store, but are left in place as the
# worker-side safety net; not dropped here because that constructor would just recreate them.


def ensure_indexes(db) -> dict:
    """Create every index in `_INDEXES` if missing. Each is wrapped so one failure (e.g. a
    unique index on legacy duplicate data) never blocks the others or app startup."""
    results: dict[str, str] = {}
    for coll, keys, opts in _INDEXES:
        label = f"{coll}:{'+'.join(k for k, _ in keys)}"
        try:
            results[label] = db[coll].create_index(keys, **opts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_indexes: %s failed: %s", label, exc)
            results[label] = f"ERROR: {exc}"
    return results


def drop_redundant_indexes(db) -> dict:
    """Drop the audited never-used indexes. Safe to run repeatedly — a missing index just
    skips. NOT called at app startup (ensure_indexes is create-only); run it via the
    standalone script when you want the cleanup."""
    results: dict[str, str] = {}
    for coll, keys in _REDUNDANT:
        label = f"{coll}:{'+'.join(k for k, _ in keys)}"
        try:
            db[coll].drop_index(keys)
            results[label] = "dropped"
        except Exception as exc:  # noqa: BLE001 — most commonly "index not found"
            results[label] = f"skip ({exc})"
    return results


def _connect_db():
    from pymongo import MongoClient

    from app.settings import settings

    client = MongoClient(settings.MONGODB_URL)
    return client[settings.MONGODB_DATABASE]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = _connect_db()
    print(f"→ applying indexes to '{db.name}'\n")
    dropped = drop_redundant_indexes(db)
    created = ensure_indexes(db)
    print("Dropped redundant:")
    for label, res in dropped.items():
        print(f"  {label}: {res}")
    print("\nEnsured indexes:")
    for label, res in created.items():
        print(f"  {label}: {res}")
    print(f"\n✓ {len(created)} indexes ensured, {sum(1 for v in dropped.values() if v == 'dropped')} redundant dropped.")
