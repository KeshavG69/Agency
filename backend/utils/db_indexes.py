"""Central MongoDB index definitions — keep reads fast.

`ensure_indexes()` is idempotent (create_index is a no-op when the index already exists),
so it's safe to run on every startup. Indexes are chosen to match how each collection is
actually queried (see the comment on each). The CRM collections (opportunities/calls/
tasks/documents) already create their core indexes in crm_store; the entries here add the
read-optimizers and cover the auth-side collections, which had no app indexes.
"""
import logging

from pymongo import ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

# (collection, keys, options) — names are auto-derived (idempotent across restarts).
_INDEXES: list[tuple[str, list, dict]] = [
    # users: login + auth dependency hit find_one({email}) constantly; members listing
    # uses organizations.$elemMatch.organization_id.
    ("users", [("email", ASCENDING)], {"unique": True}),
    ("users", [("organizations.organization_id", ASCENDING)], {}),
    # organizations: slug lookups during signup/org resolution (find_one by _id is covered).
    ("organizations", [("slug", ASCENDING)], {}),
    # invitations: accept-by-token, plus list-by-org / by-email.
    ("invitations", [("token_hash", ASCENDING)], {}),
    ("invitations", [("organization_id", ASCENDING)], {}),
    ("invitations", [("email", ASCENDING)], {}),
    # refresh tokens: looked up by token_id on every refresh.
    ("refresh_tokens", [("token_id", ASCENDING)], {"unique": True}),
    # email verification + password reset: by token_hash and by email.
    ("email_verifications", [("token_hash", ASCENDING)], {}),
    ("email_verifications", [("email", ASCENDING)], {}),
    ("password_resets", [("token_hash", ASCENDING)], {}),
    ("password_resets", [("email", ASCENDING)], {}),
    # opportunities: the UI list filters by org and sorts by priority_score desc; the
    # unanalyzed batch filters by (org, analyzed_at); member visibility filters by assigned_to.
    # (org / notice_id / (org,sol#) unique already created in crm_store.)
    ("opportunities", [("organization_id", ASCENDING), ("priority_score", DESCENDING)], {}),
    ("opportunities", [("organization_id", ASCENDING), ("analyzed_at", ASCENDING)], {}),
    ("opportunities", [("assigned_to", ASCENDING)], {}),
    # outreach log: collision lookups by (org, contact_email).
    ("outreach_log", [("organization_id", ASCENDING), ("contact_email", ASCENDING)], {}),
]


def ensure_indexes(db) -> dict:
    """Create every index if missing. Each is wrapped so one failure (e.g. a unique index
    on legacy duplicate data) never blocks the others or app startup."""
    results: dict[str, str] = {}
    for coll, keys, opts in _INDEXES:
        label = f"{coll}:{'+'.join(k for k, _ in keys)}"
        try:
            results[label] = db[coll].create_index(keys, **opts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ensure_indexes: %s failed: %s", label, exc)
            results[label] = f"ERROR: {exc}"
    return results
