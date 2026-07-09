"""Celery task — crawl SharePoint structure (+ ACL) into the knowledge graph.

Triggered after the user connects SharePoint. Crawls via Microsoft GRAPH
(`sharepoint_graph_client`) — structure + per-file ACL roster (permitted_emails /
org_wide) — plus, when connected, the SharePoint REST account for EXACT site-group
member emails (Graph can't expand those). Clear-then-rebuilds so the graph always
mirrors current SharePoint.
"""
import logging

from bson import ObjectId

from app.worker import celery_app
from client.sharepoint_graph import clear_structure, upsert_structure
from utils.composio_utils import sharepoint_entity
from utils.organizations import get_organization_crud
from utils.sharepoint_graph_client import crawl_all_sites_graph, graph_account, sp_rest_account

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=20)
def sync_sharepoint_structure_task(self, organization_id: str, with_acl: bool = True) -> dict:
    """Crawl every site (structure + ACL) and rebuild ONE org's structure graph.

    Reads SharePoint through THIS ORG's own connected accounts (resolved from the org's
    SharePoint entity) and writes the result to the org's graph (`organization_id`).
    """
    org = (organization_id or "").strip()
    if not org:
        logger.warning("sync_sharepoint_structure_task missing organization_id — skipping.")
        return {"crawled": 0, "graphed": 0, "organization_id": None}

    entity = sharepoint_entity(org)
    g_account = graph_account(entity)
    if not g_account:
        logger.warning("No SharePoint (Graph) connection for org %s — an admin must connect it.", org)
        return {"crawled": 0, "graphed": 0, "organization_id": org, "no_connection": True}
    sp_account = sp_rest_account(entity) if with_acl else None
    if with_acl and not sp_account:
        logger.warning("No SharePoint REST connection for org %s — site-group grants will "
                       "degrade to org-wide (company-internal) instead of exact emails.", org)

    excluded_paths = set(get_organization_crud().get_sharepoint_excluded_paths(ObjectId(org)))
    if excluded_paths:
        logger.info("Org %s excludes %d SharePoint path(s) from ingestion", org, len(excluded_paths))

    try:
        nodes = crawl_all_sites_graph(with_acl=with_acl, account_id=g_account, sp_account=sp_account,
                                       excluded_paths=excluded_paths)
    except Exception as exc:  # transient Graph / Composio errors -> retry
        logger.warning("SharePoint crawl failed: %s", exc)
        raise self.retry(exc=exc)
    logger.info("Crawled %d SharePoint structure nodes (acl=%s, exact_acl=%s)",
               len(nodes), with_acl, bool(sp_account))

    # Safety: a zero-node result is almost always a transient all-sites failure (the crawl
    # swallows per-site errors), NOT a genuinely empty tenant. Clearing on that would WIPE the
    # org's graph, so keep the existing graph instead of rebuilding to empty.
    if not nodes:
        logger.warning("SharePoint crawl returned 0 nodes for org %s — keeping the existing "
                       "graph (likely transient, not an empty tenant).", org)
        return {"crawled": 0, "graphed": 0, "organization_id": org, "kept_existing": True}

    # Guard against a race with a concurrent disconnect: the crawl above can take a while, and
    # if an admin disconnected SharePoint mid-crawl (Composio deletes the connection + this same
    # task's own clear_structure runs on disconnect), persisting stale data now would resurrect
    # the org's graph right after they explicitly cleared it.
    if not graph_account(entity):
        logger.warning("SharePoint was disconnected mid-crawl for org %s — discarding this "
                       "crawl's results instead of resurrecting a cleared graph.", org)
        return {"crawled": len(nodes), "graphed": 0, "organization_id": org, "disconnected_mid_crawl": True}

    # Clear-then-rebuild THIS org's graph, only after a successful non-empty crawl. A write
    # failure here (e.g. a stale FalkorDB connection after several idle minutes spent on the
    # Composio/Graph crawl — see graph_store.py's health_check_interval fix) must RETRY the
    # task, not silently report "succeeded" with graphed=0 — that would look identical to a
    # healthy sync to anything watching the task result, while the graph stays empty.
    try:
        clear_structure(org)
        written = upsert_structure(nodes, org)
        logger.info("Rebuilt org %s SharePoint structure graph with %d nodes", org, written)
    except Exception as exc:  # noqa: BLE001 — transient graph connection issue -> retry
        logger.warning("SharePoint structure rebuild failed (will retry): %s", exc)
        raise self.retry(exc=exc)

    return {"crawled": len(nodes), "graphed": written, "organization_id": org}


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30, name="sharepoint.provision_bid")
def provision_bid_folders_task(self, organization_id: str, opportunity_id: str) -> dict:
    """Create the SharePoint Bid folder tree for one opportunity and store the pointer.

    Fired when an opportunity is flipped to Bid. Idempotent — skips if the opp already has
    a folder. RuntimeError (SharePoint not connected / not crawled) is treated as a graceful
    skip (no retry); transient Graph/network errors retry.
    """
    org = (organization_id or "").strip()
    from client.crm_store import get_crm_store

    crm = get_crm_store()
    opp = crm.get_opportunity(opportunity_id, org)
    if not opp:
        return {"skipped": "opportunity not found", "opportunity_id": opportunity_id}
    if opp.get("sharepoint_folder"):
        return {"skipped": "already provisioned", "opportunity_id": opportunity_id}

    from utils.sharepoint_writer import (
        SharePointWriteError,
        provision_bid_folders,
    )

    try:
        pointer = provision_bid_folders(org, opp)
    except RuntimeError as exc:  # precondition: not connected / not crawled — expected, skip
        logger.warning("Bid folder provisioning skipped for %s: %s", opportunity_id, exc)
        return {"skipped": str(exc), "opportunity_id": opportunity_id}
    except SharePointWriteError as exc:  # deterministic (bad name / 403 no scope) — surface, no retry
        logger.error("Bid folder provisioning error for %s: %s", opportunity_id, exc)
        return {"error": str(exc), "opportunity_id": opportunity_id}
    except Exception as exc:  # transient (SharePointTransientError / network) -> retry
        logger.warning("Bid folder provisioning failed (transient) for %s: %s", opportunity_id, exc)
        raise self.retry(exc=exc)

    crm.set_sharepoint_folder(opportunity_id, org, pointer)
    logger.info("Stored SharePoint folder pointer for opportunity %s", opportunity_id)
    return {"opportunity_id": opportunity_id, "folder": pointer.get("web_url")}
