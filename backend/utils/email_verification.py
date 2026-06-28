"""
Email Verification CRUD operations with MongoDB (Sync).

Handles verification token creation, validation, and user email verification.
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
_email_verification_crud = None
_lock = threading.RLock()


class EmailVerificationCRUD:
    """Email Verification CRUD operations with token hashing (Sync Singleton)"""

    def __init__(self):
        """Initialize EmailVerificationCRUD"""
        mongodb = get_mongodb_client()
        self.db = mongodb.get_database()
        self.collection = self.db["email_verifications"]
        self.users_collection = mongodb.get_users_collection()
        self.email_service = EmailService()

    @staticmethod
    def _hash_token(token: str) -> str:
        """
        Hash verification token using SHA-256
        Security: NEVER store plain tokens in database
        """
        return hashlib.sha256(token.encode()).hexdigest()

    def create_verification(
        self,
        user_id,  # Can be ObjectId or string (UUID)
        email: str
    ) -> str:
        """
        Create email verification token and send email (sync)

        Args:
            user_id: User's ObjectId
            email: User's email address

        Returns:
            Plain token (only returned once, never stored)

        Raises:
            ValueError: If user already verified or has pending verification
        """
        # Check if user already verified
        user = self.users_collection.find_one({"_id": user_id})
        if user and user.get("email_verified"):
            raise ValueError("Email already verified")

        # Check for existing pending verification (not expired)
        existing = self.collection.find_one({
            "user_id": user_id,
            "status": "pending",
            "expires_at": {"$gt": datetime.utcnow()}
        })
        if existing:
            # Delete old pending verification before creating new one
            self.collection.delete_one({"_id": existing["_id"]})

        # Generate secure random token
        token = secrets.token_urlsafe(48)  # 64 characters
        token_hash = self._hash_token(token)

        verification = {
            "user_id": user_id,
            "email": email,
            "token_hash": token_hash,  # Store hash, NOT plain token
            "status": "pending",
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(hours=24),
            "verified_at": None,
            "attempts": 0
        }

        result = self.collection.insert_one(verification)
        verification["_id"] = result.inserted_id

        # Send verification email with plain token (only time it's visible)
        try:
            self.email_service.send_verification_email(
                to_email=email,
                token=token,  # Send plain token in email
                user_name=f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
            )
        except Exception as e:
            print(f"Failed to send verification email: {e}")
            # Don't fail the verification creation if email fails

        return token

    def validate_token(self, token: str) -> dict:
        """
        Validate verification token (sync)

        Args:
            token: Plain verification token from email

        Returns:
            Verification document

        Raises:
            ValueError: If token invalid, expired, or already used
        """
        token_hash = self._hash_token(token)
        verification = self.collection.find_one({"token_hash": token_hash})

        if not verification:
            raise ValueError("Invalid verification token")

        if verification["status"] != "pending":
            raise ValueError("Verification link has already been used")

        if datetime.utcnow() > verification["expires_at"]:
            # Mark as expired
            self.collection.update_one(
                {"_id": verification["_id"]},
                {"$set": {"status": "expired"}}
            )
            raise ValueError("Verification link has expired")

        # Increment attempts counter
        self.collection.update_one(
            {"_id": verification["_id"]},
            {"$inc": {"attempts": 1}}
        )

        return verification

    def mark_verified(self, token: str, user_id) -> bool:
        """
        Mark verification as complete and update user status (sync)

        Args:
            token: Plain verification token
            user_id: User's ObjectId

        Returns:
            True if successful

        Raises:
            ValueError: If token invalid or user mismatch
        """
        token_hash = self._hash_token(token)

        # Update verification status
        result = self.collection.update_one(
            {
                "token_hash": token_hash,
                "user_id": user_id,
                "status": "pending"
            },
            {
                "$set": {
                    "status": "verified",
                    "verified_at": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise ValueError("Invalid verification token or user mismatch")

        # Update user status
        self.users_collection.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "email_verified": True,
                    "verified_at": datetime.utcnow(),
                    "status": "active",
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        return True

    def resend_verification(self, email: str) -> str:
        """
        Resend verification email to unverified user (sync)

        Args:
            email: User's email address

        Returns:
            New plain token

        Raises:
            ValueError: If user not found, already verified, or rate limited
        """
        # Find user by email
        user = self.users_collection.find_one({"email": email})
        if not user:
            raise ValueError("User not found")

        if user.get("email_verified"):
            raise ValueError("Email already verified")

        # Check rate limiting: max 3 verifications in last hour
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_count = self.collection.count_documents({
            "user_id": user["_id"],
            "created_at": {"$gte": one_hour_ago}
        })

        if recent_count >= 3:
            raise ValueError("Too many verification attempts. Please try again later.")

        # Create new verification
        return self.create_verification(user["_id"], email)

    def cleanup_expired(self):
        """Delete expired verifications older than 7 days (sync)"""
        cutoff = datetime.utcnow() - timedelta(days=7)
        result = self.collection.delete_many({
            "status": "expired",
            "expires_at": {"$lt": cutoff}
        })
        return result.deleted_count


def get_email_verification_crud() -> EmailVerificationCRUD:
    """
    Get or create EmailVerificationCRUD instance (singleton pattern)

    Returns:
        EmailVerificationCRUD instance
    """
    global _email_verification_crud
    with _lock:
        if _email_verification_crud is None:
            _email_verification_crud = EmailVerificationCRUD()
        return _email_verification_crud
