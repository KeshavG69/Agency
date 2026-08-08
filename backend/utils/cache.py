"""A small Redis cache for expensive reads that tolerate being slightly stale.

Mirrors the pattern already proven in utils/sam_gov.py (the SAM.gov entity cache), but
generic and TTL-based rather than cached-forever-and-busted-on-save.

FAILS OPEN, ALWAYS. Every function here swallows Redis errors and falls through to the
live value. A cache that can take the app down is worse than no cache — the whole point
is to remove a slow read from the hot path, not to add a new dependency to it.

Use it for reads that are expensive, hit on every page load, and change rarely:
the facet dropdown values are the canonical example — a `distinct()` across the whole
org, recomputed constantly, for a list that changes maybe once a day.

Do NOT use it for anything a user just changed and expects to see, or for anything
scoped to something other than the key you pass. Cache keys MUST include the
organization_id: a cache that leaks one org's data to another is the worst possible bug
in a multi-tenant system, and it would be entirely self-inflicted.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

import redis

from app.settings import settings

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None

# Redis db 2: 0 is the Celery broker, 1 is its result backend. Keeping the cache on its
# own db means a `FLUSHDB` here can never drop queued work.
_CACHE_DB = 2


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            f"{settings.redis_base_url}/{_CACHE_DB}", decode_responses=True
        )
    return _client


def cache_get(key: str) -> Any:
    """The cached value, or None on a miss OR any Redis trouble."""
    if not key:
        return None
    try:
        hit = _redis().get(key)
        return json.loads(hit) if hit is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache get failed for %s: %s", key, exc)
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    if not key or ttl_seconds <= 0:
        return
    try:
        _redis().setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache set failed for %s: %s", key, exc)


def cache_bust(*keys: str) -> None:
    """Drop keys explicitly — call this when the underlying data changes."""
    real = [k for k in keys if k]
    if not real:
        return
    try:
        _redis().delete(*real)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache delete failed: %s", exc)


def cached(key: str, ttl_seconds: int, producer: Callable[[], Any]) -> Any:
    """Return the cached value, or compute it, store it, and return it.

    `producer` is only called on a miss. If Redis is unreachable, `producer` is called
    every time and everything still works — just without the saving.
    """
    hit = cache_get(key)
    if hit is not None:
        return hit
    value = producer()
    if value is not None:
        cache_set(key, value, ttl_seconds)
    return value
