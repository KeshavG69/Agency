"""Celery task — crawl SharePoint structure (+ ACL) into the knowledge graph.

Triggered after the user connects SharePoint. Crawls via Microsoft GRAPH
(`sharepoint_graph_client`): structure + per-file ACL roster (permitted_emails /
org_wide), so the agent's document search can be RBAC-prefiltered per employee.
Clear-then-rebuilds so the graph always mirrors current SharePoint.
"""
import logging

from app.worker import celery_app
from client.sharepoint_graph import clear_structure, upsert_structure
from utils.composio_utils import sharepoint_entity
from utils.sharepoint_graph_client import crawl_all_sites_graph, graph_account, sp_rest_account

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=20)
def sync_sharepoint_structure_task(self, organization_id: str, with_acl: bool = True) -> dict:
    """Crawl every site (structure + ACL) via Graph and rebuild ONE org's structure graph.

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

    try:
        nodes = crawl_all_sites_graph(with_acl=with_acl, account_id=g_account, sp_account=sp_account)
    except Exception as exc:  # transient Graph / Composio errors -> retry
        logger.warning("SharePoint Graph crawl failed: %s", exc)
        raise self.retry(exc=exc)
    logger.info("Crawled %d SharePoint structure nodes (acl=%s)", len(nodes), with_acl)

    # Clear-then-rebuild THIS org's graph, only after a successful crawl.
    try:
        clear_structure(org)
        written = upsert_structure(nodes, org)
        logger.info("Rebuilt org %s SharePoint structure graph with %d nodes", org, written)
    except Exception as exc:  # noqa: BLE001
        logger.error("SharePoint structure rebuild failed: %s", exc)
        written = 0

    return {"crawled": len(nodes), "graphed": written, "organization_id": org}
