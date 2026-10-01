"""
Organization CRUD operations with MongoDB.

Provides operations for creating, reading, and updating organizations.
Uses singleton pattern with thread safety.
"""

from bson import ObjectId
from datetime import datetime
import threading
import re
from auth.database import get_mongodb_client


# Global singleton instance
_organization_crud = None
_lock = threading.RLock()


class OrganizationCRUD:
    """Organization CRUD operations with MongoDB (Sync Singleton)"""

    def __init__(self):
        """Initialize OrganizationCRUD"""
        mongodb = get_mongodb_client()
        self.db = mongodb.get_database()
        self.collection = self.db["organizations"]
        self.users_collection = mongodb.get_users_collection()

    def create_organization(self, name: str, owner_id: ObjectId) -> dict:
        """Create a new organization (async)"""

        slug = self._generate_slug(name)

        org = {
            "name": name,
            "slug": slug,
            "owner_id": owner_id,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "status": "active",
            # Stripe billing fields (set when admin adds payment method)
            "stripe_customer_id": None,
            "default_payment_method_id": None,
            # Free proposal tracking - set to true after first proposal is created
            "first_proposal_used": False,
            "settings": {
                "default_rates": {
                    "fringe": 0.247,
                    "oh_onsite": 0.0711,
                    "oh_offsite": 0.0711,
                    "ga": 0.2243,
                    "fee": 0.07,
                    "smh": 0.065,
                    "sub_fee": 0.0,
                    "ga_passthrough": 0.025
                },
                "default_escalation_rate": 0.03,
                "allow_user_rate_override": True
            },
            "subscription": {
                "plan": "free",
                "seats": 5,
                "expires_at": None
            }
        }

        result = self.collection.insert_one(org)
        org["_id"] = result.inserted_id
        return org

    def get_by_id(self, org_id: ObjectId) -> dict:
        """Get organization by ObjectId (async)"""
        return self.collection.find_one({"_id": org_id})

    def get_by_slug(self, slug: str) -> dict:
        """Get organization by slug (async)"""
        return self.collection.find_one({"slug": slug})

    def update_settings(self, org_id: ObjectId, settings: dict) -> dict:
        """Update organization settings (async)"""

        self.collection.update_one(
            {"_id": org_id},
            {
                "$set": {
                    "settings": settings,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        return self.get_by_id(org_id)

    def get_sharepoint_excluded_paths(self, org_id: ObjectId) -> list[str]:
        """Folder/library paths this org opted OUT of ingesting (ingestion is opt-OUT —
        empty/missing means "crawl everything", matching the pre-existing default)."""
        org = self.collection.find_one({"_id": org_id}, {"sharepoint_excluded_paths": 1})
        return (org or {}).get("sharepoint_excluded_paths") or []

    def set_sharepoint_excluded_paths(self, org_id: ObjectId, paths: list[str]) -> None:
        """Replace the full excluded-paths list (the picker sends the complete set each save)."""
        self.collection.update_one(
            {"_id": org_id},
            {"$set": {"sharepoint_excluded_paths": paths, "updated_at": datetime.utcnow()}},
        )

    def get_members(self, org_id: ObjectId, role: str = None) -> list:
        """Get all users in organization (async) - queries organizations array"""

        # Query the organizations array for active members
        query = {
            "organizations": {
                "$elemMatch": {
                    "organization_id": org_id,
                    "status": "active"
                }
            }
        }

        if role:
            query["organizations"]["$elemMatch"]["role"] = role

        cursor = self.users_collection.find(query).sort("firstName", 1)
        members = list(cursor)

        # Add current org role/status/joinedAt to each member for easy access
        for member in members:
            org_membership = next(
                (org for org in member.get("organizations", [])
                 if org["organization_id"] == org_id),
                None
            )
            if org_membership:
                member["role"] = org_membership["role"]
                member["status"] = org_membership["status"]
                member["joinedAt"] = org_membership.get("joinedAt")

        return members

    def set_owner(self, org_id: ObjectId, owner_id: ObjectId):
        """Update organization owner (async)"""

        self.collection.update_one(
            {"_id": org_id},
            {"$set": {"owner_id": owner_id, "updated_at": datetime.utcnow()}}
        )

    def _generate_slug(self, name: str) -> str:
        """Generate URL-friendly slug from organization name (async)"""

        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')

        # Check for uniqueness
        counter = 1
        original_slug = slug
        while self.collection.find_one({"slug": slug}):
            slug = f"{original_slug}-{counter}"
            counter += 1

        return slug


def iter_active_memberships():
    """Yield (email, organization_id) for every ACTIVE org membership of every user — the
    correct way for a beat task to fan out "one job per employee mailbox".

    WHY THIS EXISTS. A user document has NO top-level `organization_id`: membership lives in
    `organizations[]` (plus `current_organization_id`), and `organization_id` is only derived
    per-request by auth/dependencies.py. Two beat dispatchers used to query
    `{"organization_id": {"$exists": True}}`, which matches nobody — so the daily mail sweep
    and the relationship sweep silently dispatched ZERO jobs. Iterating memberships here, in
    one place, is the fix: a user in two orgs yields twice, because each org has its own
    contact graph for that mailbox.
    """
    db = get_mongodb_client().get_database()
    for user in db["users"].find(
        {"email": {"$exists": True}, "organizations.organization_id": {"$exists": True}},
        {"email": 1, "organizations": 1},
    ):
        email = (user.get("email") or "").strip().lower()
        if not email:
            continue
        for membership in user.get("organizations") or []:
            org = str(membership.get("organization_id") or "").strip()
            # A membership with no explicit status predates the field; treat it as active.
            if org and (membership.get("status") or "active") == "active":
                yield email, org


def user_display_name(email: str) -> str:
    """The user's real display name ("Rajesh Parikh"), or "" when it cannot be resolved.

    Used wherever an agent drafts a message a human will send under their own name. It
    exists because three drafting agents (outreach, reply, relationship) used to sign off with
    a "[Your Name]" placeholder — telling the model "you do not know who is sending this" while
    the acting employee's email was being passed in the whole time. A rep who sends without
    editing mails a template to a customer.

    Case-insensitive on purpose: email signup stores the address as typed (auth/crud.py does
    not lowercase it), so an exact lowercase match silently misses those users.
    """
    addr = (email or "").strip()
    if not addr:
        return ""
    import re

    user = get_mongodb_client().get_database()["users"].find_one(
        {"email": {"$regex": f"^{re.escape(addr)}$", "$options": "i"}},
        {"firstName": 1, "lastName": 1},
    ) or {}
    return " ".join(
        p for p in ((user.get("firstName") or "").strip(), (user.get("lastName") or "").strip()) if p
    )


def get_organization_crud() -> OrganizationCRUD:
    """
    Get or create OrganizationCRUD instance (singleton pattern)

    Returns:
        OrganizationCRUD instance
    """
    global _organization_crud
    with _lock:
        if _organization_crud is None:
            _organization_crud = OrganizationCRUD()
        return _organization_crud
