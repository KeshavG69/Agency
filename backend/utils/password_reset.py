"""
Password Reset CRUD operations with MongoDB (Sync).

Handles password reset token creation, validation, and password updates.
Uses token hashing for security - plain tokens never stored in database.
"""

from bson import ObjectId
from datetime import datetime, timedelta
import secrets
import hashlib
import threading
from auth.database import get_mongodb_client
from client.email_service import EmailService
import bcrypt


# Global singleton instance
_password_reset_crud = None
_lock = threading.RLock()


class PasswordResetCRUD:
    """Password Reset CRUD operations with token hashing (Sync Singleton)"""

    def __init__(self):
        """Initialize PasswordResetCRUD"""
        mongodb = get_mongodb_client()
        self.db = mongodb.get_database()
        self.collection = self.db["password_resets"]
        self.users_collection = mongodb.get_users_collection()
        self.email_service = EmailService()

    @staticmethod
    def _hash_token(token: str) -> str:
        """
        Hash reset token using SHA-256
        Security: NEVER store plain tokens in database
        """
        return hashlib.sha256(token.encode()).hexdigest()

    def create_reset_request(self, email: str) -> str:
        """
        Create password reset token and send email (sync)

        Args:
            email: User's email address

        Returns:
            Plain token (only returned once, never stored)

        Raises:
            ValueError: If user not found or rate limited
        """
        # Find user by email
        user = self.users_collection.find_one({"email": email})
        if not user:
            # Don't reveal if user exists (security: prevent user enumeration)
            # Return success anyway but don't send email
            return None

        # Check rate limiting: max 3 requests in last hour
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_count = self.collection.count_documents({
            "user_id": user["_id"],
            "created_at": {"$gte": one_hour_ago}
        })

        if recent_count >= 3:
            raise ValueError("Too many password reset requests. Please try again later.")

        # Invalidate any existing pending reset requests for this user
        self.collection.update_many(
            {
                "user_id": user["_id"],
                "status": "pending"
            },
            {
                "$set": {"status": "cancelled"}
            }
        )

        # Generate secure random token
        token = secrets.token_urlsafe(48)  # 64 characters
        token_hash = self._hash_token(token)

        reset_request = {
            "user_id": user["_id"],
            "email": email,
            "token_hash": token_hash,  # Store hash, NOT plain token
            "status": "pending",
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(hours=1),  # 1 hour expiry
            "used_at": None,
            "attempts": 0
        }

        result = self.collection.insert_one(reset_request)
        reset_request["_id"] = result.inserted_id

        # Send password reset email with plain token
        try:
            self.email_service.send_password_reset_email(
                to_email=email,
                token=token,  # Send plain token in email
                user_name=f"{user.get('firstName', '')} {user.get('lastName', '')}".strip()
            )
        except Exception as e:
            print(f"Failed to send password reset email: {e}")
            # Don't fail the request creation if email fails

        return token

    def validate_token(self, token: str) -> dict:
        """
        Validate password reset token (sync)

        Args:
            token: Plain reset token from email

        Returns:
            Reset request document

        Raises:
            ValueError: If token invalid, expired, or already used
        """
        token_hash = self._hash_token(token)
        reset_request = self.collection.find_one({"token_hash": token_hash})

        if not reset_request:
            raise ValueError("Invalid password reset token")

        if reset_request["status"] != "pending":
            raise ValueError("Password reset link has already been used")

        if datetime.utcnow() > reset_request["expires_at"]:
            # Mark as expired
            self.collection.update_one(
                {"_id": reset_request["_id"]},
                {"$set": {"status": "expired"}}
            )
            raise ValueError("Password reset link has expired")

        # Increment attempts counter
        self.collection.update_one(
            {"_id": reset_request["_id"]},
            {"$inc": {"attempts": 1}}
        )

        return reset_request

    def reset_password(self, token: str, new_password: str) -> bool:
        """
        Reset user password and mark token as used (sync)

        Args:
            token: Plain reset token
            new_password: New password (plain text, will be hashed)

        Returns:
            True if successful

        Raises:
            ValueError: If token invalid or password validation fails
        """
        # Validate password strength
        if len(new_password) < 8:
            raise ValueError("Password must be at least 8 characters long")

        token_hash = self._hash_token(token)

        # Validate token and get user
        reset_request = self.collection.find_one({"token_hash": token_hash})
        if not reset_request:
            raise ValueError("Invalid password reset token")

        if reset_request["status"] != "pending":
            raise ValueError("Password reset link has already been used")

        if datetime.utcnow() > reset_request["expires_at"]:
            self.collection.update_one(
                {"_id": reset_request["_id"]},
                {"$set": {"status": "expired"}}
            )
            raise ValueError("Password reset link has expired")

        # Hash new password
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())

        # Update user password
        result = self.users_collection.update_one(
            {"_id": reset_request["user_id"]},
            {
                "$set": {
                    "password": hashed_password.decode('utf-8'),
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise ValueError("Failed to update password")

        # Mark reset request as used
        self.collection.update_one(
            {"_id": reset_request["_id"]},
            {
                "$set": {
                    "status": "used",
                    "used_at": datetime.utcnow()
                }
            }
        )

        return True

    def cleanup_expired(self):
        """Delete expired reset requests older than 7 days (sync)"""
        cutoff = datetime.utcnow() - timedelta(days=7)
        result = self.collection.delete_many({
            "status": {"$in": ["expired", "used", "cancelled"]},
            "created_at": {"$lt": cutoff}
        })
        return result.deleted_count


def get_password_reset_crud() -> PasswordResetCRUD:
    """
    Get or create PasswordResetCRUD instance (singleton pattern)

    Returns:
        PasswordResetCRUD instance
    """
    global _password_reset_crud
    with _lock:
        if _password_reset_crud is None:
            _password_reset_crud = PasswordResetCRUD()
        return _password_reset_crud
