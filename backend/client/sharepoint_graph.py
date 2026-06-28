"""SharePoint structure knowledge graph (separate FalkorDB graph).

One label `:SPNode {id, type, name, path, web_url, drive_id, site_id, ext, size,
permitted_emails, org_wide, unique_acl}` with `type` in {site, library, folder,
file}, connected by `[:CONTAINS]` edges (parent → child). The Mail/Proposal agent
traverses this to locate templates / past-performance / proposal material.

PER-ORG GRAPH NAME: each org's SharePoint structure is its OWN FalkorDB graph,
`sharepoint_structure_<org_id>`, so orgs are physically separate + identifiable.
`organization_id` selects the graph. PER-EMPLOYEE access within an org is enforced
by the ACL on each node (`permitted_emails` / `org_wide`) — see the `_ACCESS`
predicate, applied on every retrieval path.
"""
from __future__ import annotations

import logging
import re

from app.settings import settings
from client.graph_store import _embed_query, _embedder, _get_db

logger = logging.getLogger(__name__)
_indexed_graphs: set[str] = set()  # graph names whose indexes are ensured
_EMB_DIM = 1536  # text-embedding-3-small

# RBAC predicate: a node is readable if it's tenant-wide OR the employee is on its
# roster. Applied as a PRE-FILTER on every retrieval path (keyword, vector, expansion).
_ACCESS = "(coalesce(n.org_wide,false) = true OR $email IN coalesce(n.permitted_emails,[]))"
_ACCESS_D = "(coalesce(d.org_wide,false) = true OR $email IN coalesce(d.permitted_emails,[]))"


def _sp_graph_name(organization_id: str) -> str:
    """Per-ORG SharePoint graph name, e.g. `sharepoint_structure_<org_id>`."""
    org = re.sub(r"[^A-Za-z0-9_]", "_", (organization_id or "").strip()) or "default"
    return f"{settings.SHAREPOINT_GRAPH_NAME}_{org}"


def _graph(organization_id: str):
    """Return ONE org's SharePoint graph handle, ensuring indexes exist once per graph."""
    name = _sp_graph_name(organization_id)
    g = _get_db().select_graph(name)
    if name not in _indexed_graphs:
        try:
            g.create_node_range_index("SPNode", "id")
        except Exception:
            pass
        try:
            g.create_node_vector_index("SPNode", "embedding", dim=_EMB_DIM, similarity_function="cosine")
        except Exception:
            pass
        _indexed_graphs.add(name)
    return g


def _node_text(n: dict) -> str:
    """The text we embed for a node — type, name, and full path give semantic context."""
    return f"{n.get('type')}: {n.get('name')} | {n.get('path')}"


def clear_structure(organization_id: str) -> None:
    _graph(organization_id).query("MATCH (n) DETACH DELETE n")


def upsert_structure(nodes: list[dict], organization_id: str) -> int:
    """Upsert structure nodes + CONTAINS edges into ONE org's graph. Idempotent (MERGE on id)."""
    if not nodes:
        return 0
    rows = [
        {
            "id": n["id"], "type": n["type"], "name": n.get("name"), "path": n.get("path"),
            "web_url": n.get("web_url"), "drive_id": n.get("drive_id"),
            "site_id": n.get("site_id"), "ext": n.get("ext"), "size": n.get("size"),
            "item_count": n.get("item_count"), "list_template": n.get("list_template"),
            "parent_id": n.get("parent_id"),
            # ACL roster (Graph): who can read this node + tenant-wide flag
            "permitted_emails": n.get("permitted_emails") or [],
            "org_wide": bool(n.get("org_wide")),
            "unique_acl": bool(n.get("unique_acl")),
        }
        for n in nodes
    ]
    # Embeddings for semantic search (best-effort; skipped if no OpenAI key).
    if settings.OPENAI_API_KEY:
        try:
            embs = _embedder().embed_documents([_node_text(n) for n in nodes])
            for r, e in zip(rows, embs):
                r["embedding"] = e
        except Exception as ex:  # noqa: BLE001
            logger.warning("SharePoint node embedding failed: %s", ex)

    g = _graph(organization_id)
    g.query(
        """
        UNWIND $rows AS r
        MERGE (n:SPNode {id: r.id})
          SET n.type = r.type, n.name = r.name, n.path = r.path, n.web_url = r.web_url,
              n.drive_id = r.drive_id, n.site_id = r.site_id, n.ext = r.ext, n.size = r.size,
              n.item_count = r.item_count, n.list_template = r.list_template,
              n.permitted_emails = r.permitted_emails, n.org_wide = r.org_wide,
              n.unique_acl = r.unique_acl
        FOREACH (_ IN CASE WHEN r.embedding IS NULL THEN [] ELSE [1] END |
          SET n.embedding = vecf32(r.embedding)
        )
        """,
        params={"rows": rows},
    )
    g.query(
        """
        UNWIND $rows AS r
        WITH r WHERE r.parent_id IS NOT NULL
        MATCH (p:SPNode {id: r.parent_id}), (c:SPNode {id: r.id})
        MERGE (p)-[:CONTAINS]->(c)
        """,
        params={"rows": rows},
    )
    return len(rows)


def stats(organization_id: str) -> dict:
    g = _graph(organization_id)

    def c(t: str) -> int:
        return g.ro_query("MATCH (n:SPNode {type:$t}) RETURN count(n)", params={"t": t}).result_set[0][0]

    return {"sites": c("site"), "libraries": c("library"), "folders": c("folder"), "files": c("file")}


def get_structure(organization_id: str, employee_email: str | None = None) -> dict:
    """ONE org's structure as { nodes, edges } for the UI.

    With `employee_email`, RBAC-filters to nodes that employee may read (same ACL as
    the agent's search) — and an edge is kept only when BOTH endpoints are accessible,
    so the document tree never reveals a folder/file the employee can't see. Without
    an email (admin), returns the org's whole structure.
    """
    g = _graph(organization_id)
    email = (employee_email or "").lower()
    if email:
        node_q = (f"MATCH (n:SPNode) WHERE {_ACCESS} "
                  "RETURN n.id, n.type, n.name, n.path, n.ext, n.web_url, n.item_count")
        edge_q = (
            "MATCH (a:SPNode)-[:CONTAINS]->(b:SPNode) "
            "WHERE (coalesce(a.org_wide,false) = true OR $email IN coalesce(a.permitted_emails,[])) "
            "AND (coalesce(b.org_wide,false) = true OR $email IN coalesce(b.permitted_emails,[])) "
            "RETURN a.id, b.id"
        )
        params = {"email": email}
    else:
        node_q = "MATCH (n:SPNode) RETURN n.id, n.type, n.name, n.path, n.ext, n.web_url, n.item_count"
        edge_q = "MATCH (a:SPNode)-[:CONTAINS]->(b:SPNode) RETURN a.id, b.id"
        params = {}
    nodes = [
        {"id": r[0], "type": r[1], "name": r[2], "path": r[3], "ext": r[4],
         "web_url": r[5], "item_count": r[6]}
        for r in g.ro_query(node_q, params=params).result_set
    ]
    edges = [
        {"source": r[0], "target": r[1]}
        for r in g.ro_query(edge_q, params=params).result_set
    ]
    return {"nodes": nodes, "edges": edges}


def search_structure(terms: list[str], organization_id: str, limit: int = 25) -> list[dict]:
    """Find folders/files whose name or path matches any term — the proposal agent's tool."""
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return []
    g = _graph(organization_id)
    res = g.ro_query(
        """
        UNWIND $terms AS term
        MATCH (n:SPNode)
        WHERE n.type IN ['folder', 'file', 'list']
          AND (toLower(coalesce(n.name,'')) CONTAINS term OR toLower(coalesce(n.path,'')) CONTAINS term)
        RETURN DISTINCT n.type, n.name, n.path, n.ext, n.web_url
        LIMIT $limit
        """,
        params={"terms": terms, "limit": limit},
    )
    return [
        {"type": r[0], "name": r[1], "path": r[2], "ext": r[3], "web_url": r[4]}
        for r in res.result_set
    ]


def reindex_embeddings(organization_id: str, batch: int = 200) -> int:
    """Embed all existing nodes in one org's graph (for nodes crawled before embeddings)."""
    if not settings.OPENAI_API_KEY:
        return 0
    g = _graph(organization_id)
    nodes = [
        {"id": r[0], "type": r[1], "name": r[2], "path": r[3]}
        for r in g.ro_query("MATCH (n:SPNode) RETURN n.id, n.type, n.name, n.path").result_set
    ]
    done = 0
    for i in range(0, len(nodes), batch):
        chunk = nodes[i : i + batch]
        embs = _embedder().embed_documents([_node_text(n) for n in chunk])
        payload = [{"id": n["id"], "embedding": e} for n, e in zip(chunk, embs)]
        g.query(
            "UNWIND $rows AS r MATCH (n:SPNode {id:r.id}) SET n.embedding = vecf32(r.embedding)",
            params={"rows": payload},
        )
        done += len(chunk)
    return done


def _keyword_nodes(terms: list[str], organization_id: str, limit: int = 12,
                   employee_email: str | None = None) -> list[dict]:
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return []
    g = _graph(organization_id)
    acl = f"  AND {_ACCESS}\n" if employee_email else ""
    res = g.ro_query(
        f"""
        UNWIND $terms AS term
        MATCH (n:SPNode)
        WHERE n.type IN ['folder', 'file', 'list', 'library']
          AND (toLower(coalesce(n.name,'')) CONTAINS term OR toLower(coalesce(n.path,'')) CONTAINS term)
{acl}        RETURN DISTINCT n.id, n.type, n.name, n.path, n.web_url, n.item_count
        LIMIT $limit
        """,
        params={"terms": terms, "limit": limit, "email": (employee_email or "").lower()},
    )
    return [
        {"id": r[0], "type": r[1], "name": r[2], "path": r[3], "web_url": r[4], "item_count": r[5]}
        for r in res.result_set
    ]


def semantic_search_structure(query: str, organization_id: str, k: int = 12,
                              employee_email: str | None = None) -> list[dict]:
    """Vector (semantic) search over node embeddings. With employee_email, RBAC-PREFILTER
    then rank by cosine distance (FalkorDB's vector index can't filter inline)."""
    vec = _embed_query(query)
    if vec is None:
        return []
    g = _graph(organization_id)
    try:
        if employee_email:
            # filter-then-rank: only the employee's accessible nodes are ever ranked
            res = g.ro_query(
                f"""
                MATCH (n:SPNode)
                WHERE n.embedding IS NOT NULL
                  AND n.type IN ['folder','file','list','library']
                  AND {_ACCESS}
                RETURN n.id, n.type, n.name, n.path, n.web_url, n.item_count,
                       vec.cosineDistance(n.embedding, vecf32($vec)) AS dist
                ORDER BY dist ASC
                LIMIT $k
                """,
                params={"k": k, "vec": vec, "email": employee_email.lower()},
            )
        else:
            res = g.ro_query(
                """
                CALL db.idx.vector.queryNodes('SPNode', 'embedding', $k, vecf32($vec))
                YIELD node, score
                RETURN node.id, node.type, node.name, node.path, node.web_url, node.item_count, score
                """,
                params={"k": k, "vec": vec},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("SharePoint vector search failed: %s", e)
        return []
    return [
        {"id": r[0], "type": r[1], "name": r[2], "path": r[3], "web_url": r[4],
         "item_count": r[5], "score": r[6]}
        for r in res.result_set
    ]


def search_structure_hybrid(query: str, organization_id: str, k: int = 12,
                            employee_email: str | None = None) -> list[dict]:
    """Keyword (CONTAINS) + semantic (vector), merged + deduped. RBAC-prefiltered when
    employee_email is given (the agent only ever sees what the acting employee can read)."""
    merged: dict[str, dict] = {}
    for c in _keyword_nodes(query.split(), organization_id, limit=k, employee_email=employee_email):
        merged[c["id"]] = c
    for c in semantic_search_structure(query, organization_id, k=k, employee_email=employee_email):
        merged.setdefault(c["id"], c)
    return list(merged.values())


def search_sharepoint(query: str, employee_email: str | None = None,
                      organization_id: str = "") -> str:
    """Search ONE org's SharePoint document/list structure for material relevant to `query`.

    Pass topic / capability / document-type terms (e.g. "past performance cybersecurity",
    "capability statement", "proposal template"). Combines keyword + semantic search, then
    FOLLOWS THE GRAPH from each top match. When `employee_email` is given, results are
    RBAC-prefiltered to documents that employee may read. Returns a JSON list. Returns [].
    """
    import json

    hits = search_structure_hybrid(query, organization_id, k=10, employee_email=employee_email)
    out = []
    for h in hits[:6]:
        ctx = {
            "type": h["type"], "name": h["name"], "path": h["path"],
            "web_url": h.get("web_url"), "item_count": h.get("item_count"),
        }
        if h["type"] in ("folder", "library", "site", "list"):
            ctx["contents"] = node_contents(
                h["id"], organization_id, depth=2, limit=25, employee_email=employee_email
            )
        out.append(ctx)
    return json.dumps(out)


def node_contents(node_id: str, organization_id: str, depth: int = 2, limit: int = 30,
                  employee_email: str | None = None) -> list[dict]:
    """Follow the graph DOWN from a node — its descendants. The access check is applied
    to EACH descendant (not just the source), so a re-secured child inside an accessible
    folder is never leaked."""
    depth = max(1, min(int(depth), 6))
    g = _graph(organization_id)
    acl = f"  AND {_ACCESS_D}\n" if employee_email else ""
    res = g.ro_query(
        f"""
        MATCH (n:SPNode {{id: $id}})-[:CONTAINS*1..{depth}]->(d:SPNode)
        WHERE true
{acl}        RETURN d.type, d.name, d.path, d.web_url, d.item_count
        LIMIT $limit
        """,
        params={"id": node_id, "limit": limit, "email": (employee_email or "").lower()},
    )
    return [
        {"type": r[0], "name": r[1], "path": r[2], "web_url": r[3], "item_count": r[4]}
        for r in res.result_set
    ]
