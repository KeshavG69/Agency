from typing import Optional
from datetime import datetime, timedelta
from .database import get_mongodb_client
from .config import ACCESS_TOKEN_EXPIRE_MINUTES


async def add_token_to_blacklist(user_email: str, token: str) -> bool:
    """
    Add a token to the user's blacklist in their document (async)

    Args:
        user_email: Email of the user whose token should be blacklisted
        token: JWT token string to blacklist

    Returns:
        bool: True if successfully added, False otherwise
    """
    try:
        users_collection = get_mongodb_client().get_users_collection()

        # Calculate when this token expires (so we can clean it up later)
        expires_at = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

        # Add token to user's blacklisted_tokens array
        result = users_collection.update_one(
            {"email": user_email},
            {
                "$push": {
                    "blacklisted_tokens": {
                        "token": token,
                        "blacklisted_at": datetime.utcnow(),
                        "expires_at": expires_at
                    }
                },
                "$set": {
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        return result.modified_count > 0
    except Exception as e:
        print(f"Error adding token to blacklist: {e}")
        return False


async def is_token_blacklisted(user_email: str, token: str) -> bool:
    """
    Check if a token is blacklisted for a specific user (async)

    Args:
        user_email: Email of the user
        token: JWT token string to check

    Returns:
        bool: True if token is blacklisted, False otherwise
    """
    try:
        users_collection = get_mongodb_client().get_users_collection()

        # Look for user with this email who has this specific token in their blacklist
        user = users_collection.find_one({
            "email": user_email,
            "blacklisted_tokens.token": token
        })

        return user is not None
    except Exception as e:
        print(f"Error checking token blacklist: {e}")
        return False


async def cleanup_expired_tokens(user_email: str) -> int:
    """
    Remove expired tokens from a user's blacklist to keep the array clean (async)

    Args:
        user_email: Email of the user whose expired tokens should be cleaned up

    Returns:
        int: Number of expired tokens removed
    """
    try:
        users_collection = get_mongodb_client().get_users_collection()
        current_time = datetime.utcnow()

        # Remove tokens that have expired
        result = users_collection.update_one(
            {"email": user_email},
            {
                "$pull": {
                    "blacklisted_tokens": {
                        "expires_at": {"$lt": current_time}
                    }
                },
                "$set": {
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        return result.modified_count
    except Exception as e:
        print(f"Error cleaning up expired tokens: {e}")
        return 0


async def cleanup_all_expired_tokens() -> int:
    """
    Clean up expired tokens for all users (can be used for periodic maintenance) (async)

    Returns:
        int: Number of users whose expired tokens were cleaned up
    """
    try:
        users_collection = get_mongodb_client().get_users_collection()
        current_time = datetime.utcnow()

        # Remove expired tokens from all users
        result = users_collection.update_many(
            {"blacklisted_tokens.expires_at": {"$lt": current_time}},
            {
                "$pull": {
                    "blacklisted_tokens": {
                        "expires_at": {"$lt": current_time}
                    }
                },
                "$set": {
                    "updatedAt": datetime.utcnow()
                }
            }
        )

        return result.modified_count
    except Exception as e:
        print(f"Error cleaning up all expired tokens: {e}")
        return 0
