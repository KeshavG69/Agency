"""
Invitation CRUD operations with MongoDB (Sync).

Handles invitation creation, validation, acceptance, and revocation.
Uses token hashing for security - plain tokens never stored in database.
"""

from bson import ObjectId
from datetime import datetime, timedelta
import secrets
import hashlib
import threading
from auth.database import get_mongodb_client
from client.email_service import EmailService


# Global singleton instance
_invitation_crud = None
_lock = threading.RLock()


class InvitationCRUD:
    """Invitation CRUD operations with token hashing (Sync Singleton)"""

    def __init__(self):
        """Initialize InvitationCRUD"""
        mongodb = get_mongodb_client()
        self.db = mongodb.get_database()
        self.collection = self.db["invitations"]
        self.users_collection = mongodb.get_users_collection()
        self.orgs_collection = self.db["organizations"]
        self.email_service = EmailService()

    @staticmethod
    def _hash_token(token: str) -> str:
        """
        Hash invitation token using SHA-256
        Security: NEVER store plain tokens in database
        """
        return hashlib.sha256(token.encode()).hexdigest()

    def create_invitation(
        self,
        org_id: ObjectId,
        email: str,
        role: str,
        invited_by: ObjectId,
        proposal_ids: list = None
    ) -> dict:
        """Create and send invitation (sync)"""
        

        # Check for duplicate pending invitation
        existing = self.collection.find_one({
            "organization_id": org_id,
            "email": email,
            "status": "pending"
        })
        if existing:
            raise ValueError("User already has a pending invitation")

        # Check if user already exists in THIS organization (using organizations array)
        # Exclude users with status 'removed' - they can be re-invited
        existing_user = self.users_collection.find_one({
            "email": email,
            "organizations": {
                "$elemMatch": {
                    "organization_id": org_id,
                    "status": {"$ne": "removed"}
                }
            }
        })
        if existing_user:
            raise ValueError("User is already a member of this organization")

        # Generate secure random token
        token = secrets.token_urlsafe(48)  # 64 characters
        token_hash = self._hash_token(token)

        # Get inviter and org details for email
        inviter = self.users_collection.find_one({"_id": invited_by})
        org = self.orgs_collection.find_one({"_id": org_id})

        invitation = {
            "organization_id": org_id,
            "email": email,
            "role": role,
            "invited_by": invited_by,
            "invited_by_name": f"{inviter['firstName']} {inviter['lastName']}",
            "organization_name": org["name"],
            "token_hash": token_hash,  # Store hash, NOT plain token
            "status": "pending",
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=7),
            "accepted_at": None,
            "proposal_ids": proposal_ids or []  # Proposals to grant access to on acceptance
        }

        result = self.collection.insert_one(invitation)
        invitation["_id"] = result.inserted_id

        # Send email with plain token (only time it's visible)
        try:
            self.email_service.send_invitation_email(
                to_email=email,
                token=token,  # Send plain token in email
                organization_name=org["name"],
                invited_by_name=invitation["invited_by_name"]
            )
        except Exception as e:
            print(f"Failed to send invitation email: {e}")
            # Don't fail the invitation creation if email fails

        return invitation

    def validate_token(self, token: str) -> dict:
        """Validate invitation token (sync)"""
        

        token_hash = self._hash_token(token)
        invitation = self.collection.find_one({"token_hash": token_hash})

        if not invitation:
            raise ValueError("Invalid invitation token")

        if invitation["status"] != "pending":
            raise ValueError("Invitation has already been used")

        if datetime.utcnow() > invitation["expires_at"]:
            # Mark as expired
            self.collection.update_one(
                {"_id": invitation["_id"]},
                {"$set": {"status": "expired"}}
            )
            raise ValueError("Invitation has expired")

        return invitation

    def accept_invitation(self, token: str, user_id: ObjectId):
        """Mark invitation as accepted (sync)"""
        

        token_hash = self._hash_token(token)
        result = self.collection.update_one(
            {"token_hash": token_hash, "status": "pending"},
            {
                "$set": {
                    "status": "accepted",
                    "accepted_by": user_id,
                    "accepted_at": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise ValueError("Invalid or already used invitation")

    def get_pending(self, org_id: ObjectId) -> list:
        """Get all pending invitations for organization (sync)"""
        cursor = self.collection.find({
            "organization_id": org_id,
            "status": "pending"
        }).sort("created_at", -1)

        return list(cursor)

    def revoke_invitation(self, invitation_id: ObjectId, org_id: ObjectId) -> bool:
        """Revoke/cancel invitation (sync)"""
        

        result = self.collection.update_one(
            {
                "_id": invitation_id,
                "organization_id": org_id,
                "status": "pending"
            },
            {
                "$set": {
                    "status": "revoked",
                    "revoked_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count > 0


def get_invitation_crud() -> InvitationCRUD:
    """
    Get or create InvitationCRUD instance (singleton pattern)

    Returns:
        InvitationCRUD instance
    """
    global _invitation_crud
    with _lock:
        if _invitation_crud is None:
            _invitation_crud = InvitationCRUD()
        return _invitation_crud
