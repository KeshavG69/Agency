"""FastAPI app creation, middleware, and router registration."""
import utils.agno_patches  # noqa: F401  -- apply agno reasoning patch before any agent runs
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from routers import (
    actions,
    auth,
    calls,
    composio,
    contacts,
    documents,
    ingestion,
    intelligence,
    invitations,
    mail,
    mail_triage,
    opportunities,
    organizations,
    sharepoint,
    users,
    webhooks,
    workspace,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB indexes exist (idempotent) so reads stay fast.
    try:
        from auth.database import get_mongodb_client
        from utils.db_indexes import ensure_indexes

        ensure_indexes(get_mongodb_client().get_database())
    except Exception as exc:  # noqa: BLE001 — never block startup on index creation
        import logging

        logging.getLogger(__name__).warning("ensure_indexes on startup failed: %s", exc)

    _warm_graph_connections()
    yield


def _warm_graph_connections() -> None:
    """Open the FalkorDB connection and ensure each org's graph indexes, off the request path.

    Both graph stores build their handle lazily and, on the FIRST access per graph name, fire
    five index-creation round trips (four range + one vector for contacts, one for SharePoint)
    before answering. That work is idempotent and cheap to repeat — but it was being paid
    INSIDE whichever user request happened to arrive first, which measured 3.8-4.6s against
    0.4-0.8s once warm, on graphs holding almost no data. The first person to open Contacts
    after a deploy ate the whole setup cost and read it as "the graph is slow".

    Runs on a daemon thread so a graph database that is down or unreachable delays nothing and
    fails nothing: the lazy path still works exactly as before, it just may not be warm.
    """
    import logging
    import threading

    log = logging.getLogger(__name__)

    def _warm() -> None:
        try:
            from auth.database import get_mongodb_client
            from client.graph_store import get_graph
            from client.sharepoint_graph import _graph as sp_graph

            orgs = get_mongodb_client().get_database()["organizations"].find({}, {"_id": 1})
            for org in orgs:
                oid = str(org["_id"])
                for fn in (get_graph, sp_graph):
                    try:
                        fn(oid)
                    except Exception as exc:  # noqa: BLE001 — one bad org must not stop the rest
                        log.debug("graph warm-up skipped for %s: %s", oid, exc)
        except Exception as exc:  # noqa: BLE001 — warming is an optimisation, never a gate
            log.warning("graph warm-up failed (falling back to lazy connect): %s", exc)

    threading.Thread(target=_warm, name="graph-warmup", daemon=True).start()


app = FastAPI(
    title="Collecct API",
    description="Collecct — opportunity pipeline + AI agents over MongoDB.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


app.include_router(ingestion.router)
app.include_router(opportunities.router)
app.include_router(documents.router)
app.include_router(calls.router)
app.include_router(actions.router)
app.include_router(composio.router)
app.include_router(contacts.router)
app.include_router(intelligence.router)
app.include_router(sharepoint.router)
app.include_router(mail.router)
app.include_router(mail_triage.router)
app.include_router(webhooks.router)
# --- authentication / org-tenancy (ported from PriceIQ) ---
app.include_router(auth.router, prefix="/api", tags=["authentication"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(invitations.router, tags=["invitations"])
app.include_router(workspace.router, tags=["workspace"])
app.include_router(organizations.router, tags=["organizations"])
