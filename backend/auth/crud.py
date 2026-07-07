from typing import Optional, List
from datetime import datetime
from bson import ObjectId
import bcrypt
import threading
from .database import get_mongodb_client
from .models import UserSignup, UserResponse, GoogleUserProfile, MicrosoftUserProfile
from .utils import hash_password, verify_password, generate_user_id


# Global singleton instance
_user_crud = None
_lock = threading.RLock()


class UserCRUD:
    """Sync UserCRUD with PyMongo driver"""

    def __init__(self):
        """Initialize UserCRUD"""
        self.mongodb = get_mongodb_client()
        self.collection = self.mongodb.get_users_collection()
        self.db = self.mongodb.get_database()
    @staticmethod
    def create_user(user_data: UserSignup, email_verified: bool = True) -> UserResponse:
        """Create a new user with default organization"""
        from utils.organizations import get_organization_crud
        from auth import config

        mongodb = get_mongodb_client()
        users_collection = mongodb.get_users_collection()

        # Check if user already exists
        existing_user = users_collection.find_one({"email": user_data.email})
        if existing_user:
            raise ValueError("User with this email already exists")

        # Create user ID
        user_id = generate_user_id()
        hashed_password = hash_password(user_data.password)
        now = datetime.utcnow()

        # Create organization with slug as name
        org_crud = get_organization_crud()
        temp_name = f"{user_data.firstName}-{user_data.lastName}-org"
        organization = org_crud.create_organization(temp_name, user_id)
        org_id = organization["_id"]

        # Update organization name to match slug (unique name)
        db = mongodb.get_database()
        db.organizations.update_one(
            {"_id": org_id},
            {"$set": {"name": organization["slug"]}}
        )

        # Determine user status based on email verification
        user_status = "active" if email_verified else "pending_verification"

        user_doc = {
            "_id": user_id,
            "firstName": user_data.firstName,
            "lastName": user_data.lastName,
            "email": user_data.email,
            "password": hashed_password,
            "auth_method": "email",
            "email_verified": email_verified,
            "verified_at": now if email_verified else None,
            "status": user_status,
            "current_organization_id": org_id,
            "organizations": [{
                "organization_id": org_id,
                "role": "admin",
                "status": "active",
                "joinedAt": now
            }],
            "terms_accepted_version": config.CURRENT_TERMS_VERSION,
            "terms_accepted_at": now,
            "createdAt": now,
            "updatedAt": now
        }

        # Insert user into database
        result = users_collection.insert_one(user_doc)

        if result.inserted_id:
            return UserResponse(
                id=user_id,
                firstName=user_data.firstName,
                lastName=user_data.lastName,
                email=user_data.email,
                createdAt=user_doc["createdAt"]
            )
        else:
            raise Exception("Failed to create user")

    @staticmethod
    def authenticate_user(email: str, password: str) -> Optional[UserResponse]:
        """Authenticate user with email and password (async)"""
        users_collection = get_mongodb_client().get_users_collection()

        # Find user by email
        user_doc = users_collection.find_one({"email": email})
        if not user_doc:
            return None

        # Gate on whether a password is actually set, not on `auth_method` — a user who signs
        # in with Google/Microsoft keeps their original password (create_or_update_*_user
        # never touches or clears it), so both methods should keep working side by side. Only
        # a genuinely password-less OAuth-only account (no `password` field at all) is rejected
        # here, since there'd be nothing to verify against.
        if not user_doc.get("password"):
            return None

        # Verify password
        if not verify_password(password, user_doc["password"]):
            return None

        return UserResponse(
            id=str(user_doc["_id"]),
            firstName=user_doc["firstName"],
            lastName=user_doc["lastName"],
            email=user_doc["email"],
            organization_id=str(user_doc.get("organization_id")) if user_doc.get("organization_id") else None,
            role=user_doc.get("role"),
            status=user_doc.get("status"),
            createdAt=user_doc["createdAt"]
        )

    @staticmethod
    def get_user_by_email(email: str) -> Optional[UserResponse]:
        """Get user by email (async)"""
        users_collection = get_mongodb_client().get_users_collection()

        user_doc = users_collection.find_one({"email": email})
        if not user_doc:
            return None

        return UserResponse(
            id=str(user_doc["_id"]),
            firstName=user_doc["firstName"],
            lastName=user_doc["lastName"],
            email=user_doc["email"],
            organization_id=str(user_doc.get("organization_id")) if user_doc.get("organization_id") else None,
            role=user_doc.get("role"),
            status=user_doc.get("status"),
            createdAt=user_doc["createdAt"]
        )

    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[UserResponse]:
        """Get user by ID (async)"""
        users_collection = get_mongodb_client().get_users_collection()

        user_doc = users_collection.find_one({"_id": user_id})
        if not user_doc:
            return None

        return UserResponse(
            id=str(user_doc["_id"]),
            firstName=user_doc["firstName"],
            lastName=user_doc["lastName"],
            email=user_doc["email"],
            organization_id=str(user_doc.get("organization_id")) if user_doc.get("organization_id") else None,
            role=user_doc.get("role"),
            status=user_doc.get("status"),
            createdAt=user_doc["createdAt"]
        )

    @staticmethod
    def create_or_update_google_user(google_profile: GoogleUserProfile) -> UserResponse:
        """Create a new Google OAuth user or update existing one (async)"""
        from utils.organizations import get_organization_crud

        users_collection = get_mongodb_client().get_users_collection()

        # Check if user already exists by email
        existing_user = users_collection.find_one({"email": google_profile.email})

        if existing_user:
            # Update existing user with Google profile data
            update_data = {
                "google_id": google_profile.sub,
                "google_profile": {
                    "name": google_profile.name,
                    "given_name": google_profile.given_name,
                    "family_name": google_profile.family_name,
                    "picture": google_profile.picture,
                    "email_verified": google_profile.email_verified
                },
                "auth_method": "google",
                "email_verified": True,  # Mark as verified when using Google OAuth
                "status": "active",
                "updatedAt": datetime.utcnow()
            }

            # Set verified_at only if not already set
            if not existing_user.get("verified_at"):
                update_data["verified_at"] = datetime.utcnow()

            users_collection.update_one(
                {"_id": existing_user["_id"]},
                {"$set": update_data}
            )

            return UserResponse(
                id=str(existing_user["_id"]),
                firstName=existing_user.get("firstName", google_profile.given_name),
                lastName=existing_user.get("lastName", google_profile.family_name),
                email=google_profile.email,
                createdAt=existing_user["createdAt"]
            )
        else:
            # Create new Google user with organization
            user_id = generate_user_id()
            now = datetime.utcnow()

            # Create organization with slug as name
            org_crud = get_organization_crud()
            temp_name = f"{google_profile.given_name}-{google_profile.family_name}-org"
            organization = org_crud.create_organization(temp_name, user_id)
            org_id = organization["_id"]

            # Update organization name to match slug (unique name)
            db = get_mongodb_client().get_database()
            db.organizations.update_one(
                {"_id": org_id},
                {"$set": {"name": organization["slug"]}}
            )

            # Import config for terms version
            from auth import config

            user_doc = {
                "_id": user_id,
                "firstName": google_profile.given_name,
                "lastName": google_profile.family_name,
                "email": google_profile.email,
                "google_id": google_profile.sub,
                "google_profile": {
                    "name": google_profile.name,
                    "given_name": google_profile.given_name,
                    "family_name": google_profile.family_name,
                    "picture": google_profile.picture,
                    "email_verified": google_profile.email_verified
                },
                "auth_method": "google",
                "email_verified": True,  # Google OAuth users are pre-verified
                "verified_at": now,
                "status": "active",
                "current_organization_id": org_id,
                "organizations": [{
                    "organization_id": org_id,
                    "role": "admin",
                    "status": "active",
                    "joinedAt": now
                }],
                "terms_accepted_version": config.CURRENT_TERMS_VERSION,
                "terms_accepted_at": now,
                "createdAt": now,
                "updatedAt": now
            }

            # Insert user into database
            result = users_collection.insert_one(user_doc)

            if result.inserted_id:
                return UserResponse(
                    id=user_id,
                    firstName=google_profile.given_name,
                    lastName=google_profile.family_name,
                    email=google_profile.email,
                    createdAt=user_doc["createdAt"]
                )
            else:
                raise Exception("Failed to create Google user")

    @staticmethod
    def create_or_update_microsoft_user(microsoft_profile: MicrosoftUserProfile) -> UserResponse:
        """Create a new Microsoft OAuth user or update an existing one. Mirrors
        create_or_update_google_user exactly — auth_method='microsoft', no password field,
        pre-verified, open self-registration (a brand-new email gets its own organization)."""
        users_collection = get_mongodb_client().get_users_collection()

        existing_user = users_collection.find_one({"email": microsoft_profile.email})

        if existing_user:
            update_data = {
                "microsoft_id": microsoft_profile.oid,
                "microsoft_profile": {
                    "name": microsoft_profile.name,
                    "given_name": microsoft_profile.given_name,
                    "family_name": microsoft_profile.family_name,
                    "email_verified": microsoft_profile.email_verified,
                },
                "auth_method": "microsoft",
                "email_verified": True,
                "status": "active",
                "updatedAt": datetime.utcnow(),
            }
            if not existing_user.get("verified_at"):
                update_data["verified_at"] = datetime.utcnow()

            users_collection.update_one({"_id": existing_user["_id"]}, {"$set": update_data})

            return UserResponse(
                id=str(existing_user["_id"]),
                firstName=existing_user.get("firstName", microsoft_profile.given_name),
                lastName=existing_user.get("lastName", microsoft_profile.family_name),
                email=microsoft_profile.email,
                createdAt=existing_user["createdAt"],
            )

        from utils.organizations import get_organization_crud

        user_id = generate_user_id()
        now = datetime.utcnow()

        org_crud = get_organization_crud()
        temp_name = f"{microsoft_profile.given_name}-{microsoft_profile.family_name}-org"
        organization = org_crud.create_organization(temp_name, user_id)
        org_id = organization["_id"]

        db = get_mongodb_client().get_database()
        db.organizations.update_one({"_id": org_id}, {"$set": {"name": organization["slug"]}})

        from auth import config

        user_doc = {
            "_id": user_id,
            "firstName": microsoft_profile.given_name,
            "lastName": microsoft_profile.family_name,
            "email": microsoft_profile.email,
            "microsoft_id": microsoft_profile.oid,
            "microsoft_profile": {
                "name": microsoft_profile.name,
                "given_name": microsoft_profile.given_name,
                "family_name": microsoft_profile.family_name,
                "email_verified": microsoft_profile.email_verified,
            },
            "auth_method": "microsoft",
            "email_verified": True,
            "verified_at": now,
            "status": "active",
            "current_organization_id": org_id,
            "organizations": [{
                "organization_id": org_id,
                "role": "admin",
                "status": "active",
                "joinedAt": now,
            }],
            "terms_accepted_version": config.CURRENT_TERMS_VERSION,
            "terms_accepted_at": now,
            "createdAt": now,
            "updatedAt": now,
        }

        result = users_collection.insert_one(user_doc)
        if not result.inserted_id:
            raise Exception("Failed to create Microsoft user")

        return UserResponse(
            id=user_id,
            firstName=microsoft_profile.given_name,
            lastName=microsoft_profile.family_name,
            email=microsoft_profile.email,
            createdAt=user_doc["createdAt"],
        )

    def create_user_with_organization(
        self,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        organization_id: ObjectId,
        role: str = "user"
    ) -> dict:
        """Create user with organization (for invitation acceptance)"""
        from auth import config

        # Check if user already exists
        existing = self.collection.find_one({"email": email})
        if existing:
            raise ValueError("Email already registered")

        now = datetime.utcnow()
        user = {
            "_id": generate_user_id(),
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "password": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
            # Belongs ONLY to the invited org — no personal org — so role can't be
            # escalated by switching workspaces. Email ownership is proven by the invite.
            "organizations": [{
                "organization_id": organization_id,
                "role": role,
                "status": "active",
                "joinedAt": now
            }],
            "current_organization_id": organization_id,
            "auth_method": "email",
            "email_verified": True,
            "verified_at": now,
            "status": "active",
            "terms_accepted_version": config.CURRENT_TERMS_VERSION,
            "terms_accepted_at": now,
            "createdAt": now,
            "updatedAt": now
        }

        result = self.collection.insert_one(user)
        user["_id"] = result.inserted_id
        return user

    def create_microsoft_user_with_organization(
        self,
        microsoft_profile: MicrosoftUserProfile,
        organization_id: ObjectId,
        role: str = "user",
    ) -> dict:
        """Create a user via invite acceptance, authenticated by Microsoft instead of a typed
        password — mirrors create_user_with_organization exactly, minus the password field.
        Caller MUST have already verified microsoft_profile.email matches the invited email."""
        from auth import config

        existing = self.collection.find_one({"email": microsoft_profile.email})
        if existing:
            raise ValueError("Email already registered")

        now = datetime.utcnow()
        user = {
            "_id": generate_user_id(),
            "firstName": microsoft_profile.given_name,
            "lastName": microsoft_profile.family_name,
            "email": microsoft_profile.email,
            "microsoft_id": microsoft_profile.oid,
            "microsoft_profile": {
                "name": microsoft_profile.name,
                "given_name": microsoft_profile.given_name,
                "family_name": microsoft_profile.family_name,
                "email_verified": microsoft_profile.email_verified,
            },
            # Belongs ONLY to the invited org — no personal org — so role can't be
            # escalated by switching workspaces. Email ownership is proven by Microsoft here,
            # in place of the invite-flow's usual typed-password proof.
            "organizations": [{
                "organization_id": organization_id,
                "role": role,
                "status": "active",
                "joinedAt": now,
            }],
            "current_organization_id": organization_id,
            "auth_method": "microsoft",
            "email_verified": True,
            "verified_at": now,
            "status": "active",
            "terms_accepted_version": config.CURRENT_TERMS_VERSION,
            "terms_accepted_at": now,
            "createdAt": now,
            "updatedAt": now,
        }

        result = self.collection.insert_one(user)
        user["_id"] = result.inserted_id
        return user

    def get_by_id(self, user_id: ObjectId) -> Optional[dict]:
        """Get user by ObjectId"""
        return self.collection.find_one({"_id": user_id})

    def get_by_ids(self, user_ids: List[ObjectId]) -> List[dict]:
        """Batch fetch users by IDs - NEW for optimization"""
        if not user_ids:
            return []

        cursor = self.collection.find(
            {"_id": {"$in": user_ids}},
            {"password": 0}  # Exclude sensitive fields
        )

        return list(cursor)

    def remove_from_organization(self, user_id: ObjectId, org_id: ObjectId):
        """Remove user from organization (soft delete in organizations array)"""

        # Update the status to "removed" for this specific organization in the array
        self.collection.update_one(
            {
                "_id": user_id,
                "organizations.organization_id": org_id
            },
            {
                "$set": {
                    "organizations.$.status": "removed",
                    "updatedAt": datetime.utcnow()
                }
            }
        )


def get_user_crud() -> UserCRUD:
    """
    Get or create UserCRUD instance (singleton pattern)

    Returns:
        UserCRUD instance
    """
    global _user_crud
    with _lock:
        if _user_crud is None:
            _user_crud = UserCRUD()
        return _user_crud
