from pymongo import MongoClient
from typing import Optional
import threading
from app.settings import settings

# Users collection name
USERS_COLLECTION = "users"


class MongoDB:
    """MongoDB client for authentication (thread-safe singleton)"""

    def __init__(self):
        """Initialize MongoDB client"""
        if not settings.MONGODB_URL:
            raise ValueError("MONGODB_URL not configured")
        if not settings.MONGODB_DATABASE:
            raise ValueError("MONGODB_DATABASE not configured")

        self._client = None
        self._db = None
        self._lock = threading.RLock()

        # Initialize connection
        self._initialize_connection()

    def _initialize_connection(self):
        """Initialize MongoDB connection with error handling"""
        try:
            self._client = MongoClient(settings.MONGODB_URL)
            self._db = self._client[settings.MONGODB_DATABASE]

            # Test connection
            self._client.admin.command("ping")

            print("✅ MongoDB authentication client initialized")

        except Exception as e:
            print(f"❌ Failed to initialize MongoDB connection: {e}")
            raise

    def get_database(self):
        """Get database instance (thread-safe)"""
        with self._lock:
            return self._db

    def get_collection(self, collection_name: str):
        """Get a collection from the database (thread-safe)"""
        with self._lock:
            return self._db[collection_name]

    def get_users_collection(self):
        """Get the users collection (thread-safe)"""
        with self._lock:
            return self._db[USERS_COLLECTION]

    def close(self):
        """Close MongoDB connection"""
        try:
            with self._lock:
                if self._client:
                    self._client.close()
                    print("✅ MongoDB connection closed")
        except Exception as e:
            print(f"❌ Error closing MongoDB connection: {e}")


# Global singleton instance
_mongodb_client: Optional[MongoDB] = None
_client_lock = threading.RLock()


def get_mongodb_client() -> MongoDB:
    """
    Get or create MongoDB client (singleton pattern)

    Returns:
        MongoDB instance
    """
    global _mongodb_client
    with _client_lock:
        if _mongodb_client is None:
            _mongodb_client = MongoDB()
        return _mongodb_client


def close_mongodb_client():
    """Close MongoDB client connection"""
    global _mongodb_client
    with _client_lock:
        if _mongodb_client is not None:
            _mongodb_client.close()
            _mongodb_client = None
