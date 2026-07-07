"""Short-lived server-side store for the Microsoft OAuth `state` parameter.

The login-url request and the callback request are two SEPARATE HTTP calls (the browser
round-trips through Microsoft in between), so anything the callback needs to remember from
the login-url call — right now, just which invitation (if any) this round-trip is for — has
to be carried via `state` and looked up again here. A random, unguessable `state` doubles as
CSRF protection: the callback only proceeds if the state it receives was one we handed out.
"""
import secrets

import redis

from app.settings import settings

_redis: redis.Redis | None = None
_KEY = "msoauth:state:{state}"
_TTL_SECONDS = 600  # 10 minutes — comfortably longer than a real sign-in takes


def _redis_client() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(f"{settings.redis_base_url}/0", decode_responses=True)
    return _redis


def create_state(invite_token: str | None) -> str:
    """Mint a fresh, random state value and remember what it's for. Empty string (not a
    literal 'None') is stored for a plain login/signup round-trip, so a later `pop_state`
    can cleanly distinguish 'no invite' from 'lookup miss'."""
    state = secrets.token_urlsafe(32)
    _redis_client().set(_KEY.format(state=state), invite_token or "", ex=_TTL_SECONDS)
    return state


def pop_state(state: str) -> tuple[bool, str | None]:
    """Consume a state value (single-use) — returns (was_valid, invite_token_or_None).
    Deleting on read prevents a leaked/replayed state from being reused for a second login."""
    client = _redis_client()
    key = _KEY.format(state=state)
    value = client.get(key)
    if value is None:
        return False, None
    client.delete(key)
    return True, (value or None)
