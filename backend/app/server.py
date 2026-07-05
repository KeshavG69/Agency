"""FastAPI app creation, middleware, and router registration."""
import utils.agno_patches  # noqa: F401  -- apply agno reasoning patch before any agent runs
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from routers import (
    auth,
    calls,
    composio,
    contacts,
    documents,
    ingestion,
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
    yield


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
app.include_router(composio.router)
app.include_router(contacts.router)
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
