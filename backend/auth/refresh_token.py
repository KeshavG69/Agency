"""
Refresh token management utilities (Async with Motor)
Handles creation, validation, rotation, and revocation of refresh tokens
"""

import uuid
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from .config import REFRESH_TOKEN_EXPIRE_DAYS, SECRET_KEY, ALGORITHM
from .models import RefreshTokenValidationResult
from .database import get_mongodb_client
from jose import jwt, JWTError


# Helper to get refresh tokens collection
def _get_refresh_tokens_collection():
    """Get refresh_tokens collection (cached connection)"""
    db = get_mongodb_client().get_database()
    return db["refresh_tokens"]


def create_refresh_token_jwt(user_email: str, token_id: str) -> str:
    """
    Create JWT refresh token

    Args:
        user_email: User's email address
        token_id: Unique token identifier

    Returns:
        JWT refresh token string
    """
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {
        "sub": user_email,
        "jti": token_id,  # JWT ID
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "refresh"
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def create_refresh_token(
    user_email: str,
    device_info: Optional[str] = None,
    ip_address: Optional[str] = None,
    token_family_id: Optional[str] = None
) -> str:
    """
    Create a new refresh token and store it in MongoDB

    Args:
        user_email: User's email address
        device_info: Optional device/user-agent information
        ip_address: Optional IP address
        token_family_id: Optional family ID for token rotation (creates new if None)

    Returns:
        JWT refresh token string
    """
    # Generate unique IDs
    token_id = str(uuid.uuid4())
    if token_family_id is None:
        token_family_id = str(uuid.uuid4())  # New token family

    # Create JWT token
    refresh_token = create_refresh_token_jwt(user_email, token_id)

    # Hash the token for storage (using SHA256 since JWT tokens exceed bcrypt's 72-byte limit)
    token_hash = hashlib.sha256(refresh_token.encode('utf-8')).hexdigest()

    # Store in MongoDB
    expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    token_data = {
        "token_id": token_id,
        "user_email": user_email,
        "refresh_token_hash": token_hash,
        "token_family_id": token_family_id,
        "expires_at": expires_at,
        "created_at": datetime.utcnow(),
        "device_info": device_info,
        "ip_address": ip_address,
        "is_revoked": False,
        "revoked_at": None
    }

    collection = _get_refresh_tokens_collection()
    collection.insert_one(token_data)

    return refresh_token


async def validate_refresh_token(token: str) -> RefreshTokenValidationResult:
    """
    Validate a refresh token

    Args:
        token: JWT refresh token string

    Returns:
        RefreshTokenValidationResult with validation status and data
    """
    try:
        # Decode JWT
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        # Check token type
        if payload.get("type") != "refresh":
            return RefreshTokenValidationResult(
                valid=False,
                reason="Invalid token type"
            )

        user_email = payload.get("sub")
        token_id = payload.get("jti")

        if not user_email or not token_id:
            return RefreshTokenValidationResult(
                valid=False,
                reason="Missing token claims"
            )

        # Find token in database
        collection = _get_refresh_tokens_collection()
        token_data = collection.find_one({"token_id": token_id})

        if not token_data:
            return RefreshTokenValidationResult(
                valid=False,
                reason="Token not found"
            )

        # Check if token is revoked
        if token_data.get("is_revoked"):
            # Token was already used - potential security issue!
            return RefreshTokenValidationResult(
                valid=False,
                reason="token_reused",
                token_family_id=token_data.get("token_family_id")
            )

        # Check if token is expired
        if token_data.get("expires_at") < datetime.utcnow():
            return RefreshTokenValidationResult(
                valid=False,
                reason="Token expired"
            )

        # Token is valid
        return RefreshTokenValidationResult(
            valid=True,
            user_email=user_email,
            token_family_id=token_data.get("token_family_id"),
            token_id=token_id
        )

    except JWTError as e:
        return RefreshTokenValidationResult(
            valid=False,
            reason=f"JWT decode error: {str(e)}"
        )
    except Exception as e:
        return RefreshTokenValidationResult(
            valid=False,
            reason=f"Validation error: {str(e)}"
        )


async def rotate_refresh_token(
    old_token: str,
    token_family_id: str,
    device_info: Optional[str] = None,
    ip_address: Optional[str] = None
) -> str:
    """
    Rotate refresh token: invalidate old token and create new one in same family

    Args:
        old_token: Current refresh token to invalidate
        token_family_id: Token family ID to maintain
        device_info: Optional device information
        ip_address: Optional IP address

    Returns:
        New JWT refresh token string
    """
    # Decode old token to get token_id
    try:
        payload = jwt.decode(old_token, SECRET_KEY, algorithms=[ALGORITHM])
        old_token_id = payload.get("jti")
        user_email = payload.get("sub")

        # Revoke old token
        collection = _get_refresh_tokens_collection()
        collection.update_one(
            {"token_id": old_token_id},
            {
                "$set": {
                    "is_revoked": True,
                    "revoked_at": datetime.utcnow()
                }
            }
        )

        # Create new token in same family
        new_token = await create_refresh_token(
            user_email=user_email,
            device_info=device_info,
            ip_address=ip_address,
            token_family_id=token_family_id
        )

        return new_token

    except Exception as e:
        raise ValueError(f"Token rotation failed: {str(e)}")


async def revoke_refresh_token(token: str) -> bool:
    """
    Revoke a specific refresh token

    Args:
        token: JWT refresh token to revoke

    Returns:
        True if successful, False otherwise
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_id = payload.get("jti")

        collection = _get_refresh_tokens_collection()
        result = collection.update_one(
            {"token_id": token_id},
            {
                "$set": {
                    "is_revoked": True,
                    "revoked_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count > 0
    except Exception as e:
        print(f"Error revoking token: {e}")
        return False


async def revoke_refresh_token_family(token_family_id: str) -> int:
    """
    Revoke all tokens in a token family (security measure for token reuse detection)

    Args:
        token_family_id: Family ID of tokens to revoke

    Returns:
        Number of tokens revoked
    """
    try:
        collection = _get_refresh_tokens_collection()
        result = collection.update_many(
            {"token_family_id": token_family_id, "is_revoked": False},
            {
                "$set": {
                    "is_revoked": True,
                    "revoked_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count
    except Exception as e:
        print(f"Error revoking token family: {e}")
        return 0


async def revoke_user_refresh_tokens(user_email: str) -> int:
    """
    Revoke all refresh tokens for a user (logout from all devices)

    Args:
        user_email: User's email address

    Returns:
        Number of tokens revoked
    """
    try:
        collection = _get_refresh_tokens_collection()
        result = collection.update_many(
            {"user_email": user_email, "is_revoked": False},
            {
                "$set": {
                    "is_revoked": True,
                    "revoked_at": datetime.utcnow()
                }
            }
        )

        return result.modified_count
    except Exception as e:
        print(f"Error revoking user tokens: {e}")
        return 0


async def cleanup_expired_tokens() -> int:
    """
    Remove expired refresh tokens from database (maintenance task)

    Returns:
        Number of tokens deleted
    """
    try:
        collection = _get_refresh_tokens_collection()
        result = collection.delete_many({
            "expires_at": {"$lt": datetime.utcnow()}
        })

        return result.deleted_count
    except Exception as e:
        print(f"Error cleaning up expired tokens: {e}")
        return 0


def verify_refresh_token_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify and decode refresh token JWT

    Args:
        token: JWT refresh token string

    Returns:
        Token payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError:
        return None
