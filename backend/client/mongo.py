"""One MongoDB connection for the whole process.

Every store used to open its own MongoClient: twelve of them per process. A client is not just a
socket. It keeps a connection pool and its own background threads (a monitor and a round-trip
timer for EVERY server of the replica set, plus a cursor reaper), so twelve clients against a
three-node cluster meant around a hundred idle threads in the API and a hundred more in the
worker. In one container that was most of the thread limit, and the API started failing with
"RuntimeError: can't start new thread".

pymongo's client is thread-safe and meant to be shared, so there is one here and every store asks
for its database from it. Stores that want timezone-aware datetimes get them through the
database's codec options, which is what `MongoClient(tz_aware=True)` set under the hood.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from pymongo import MongoClient
from pymongo.database import Database

from app.settings import settings

_client: MongoClient | None = None
_lock = threading.Lock()


def mongo_client() -> MongoClient:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = MongoClient(settings.MONGODB_URL)
    return _client


def mongo_db(tz_aware: bool = False) -> Database:
    """The application database. `tz_aware=True` returns datetimes with UTC attached."""
    db = mongo_client()[settings.MONGODB_DATABASE]
    if tz_aware:
        return db.with_options(codec_options=db.codec_options.with_options(tz_aware=True))
    return db


# ---- running independent queries at the same time ----------------------------------------------
# A list page needs its rows, its total and its pill counts: three network-bound queries that
# cost the slowest of them when they overlap, not the sum. Each request used to build its own
# ThreadPoolExecutor for that, so forty requests in flight meant 120 extra threads on top of the
# server's own, created and torn down on every poll. One small pool, shared, does the same job
# with a fixed number of threads; when it is busy the extra queries wait their turn.
_QUERY_THREADS = 6
_pool: ThreadPoolExecutor | None = None


def _query_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(max_workers=_QUERY_THREADS, thread_name_prefix="mongo-query")
    return _pool


def together(*calls: Callable[[], Any]) -> list:
    """Run `calls` at the same time and return their results in order. The first runs on the
    calling thread, the rest on the shared pool. A call that can't be handed to the pool (the
    process is out of threads) runs here instead: slower, never an error. Calls must not use
    `together` themselves."""
    futures = []
    for call in calls[1:]:
        try:
            futures.append(_query_pool().submit(call))
        except RuntimeError:
            futures.append(None)
    results = [calls[0]()] if calls else []
    for call, future in zip(calls[1:], futures):
        results.append(future.result() if future is not None else call())
    return results
