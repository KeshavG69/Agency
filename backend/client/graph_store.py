"""FalkorDB knowledge graph — the CRM contact network.

Nodes:  (:Person {email, owner_email, name, title, ...})
        (:Company {name, owner_email})
Edges:  (:Person)-[:WORKS_AT]->(:Company)

Built from enriched Outlook contacts (Composio → Explorium). The CRM Agent later
traverses this graph to surface the valuable contacts for an opportunity. Mongo
stays the system-of-record; this graph holds ONLY the relationship network.

TWO LAYERS OF ISOLATION:
  • PER-ORG GRAPH NAME — each org's network is its OWN FalkorDB graph,
    `collecct_network_<org_id>`, so orgs are physically separate + easy to identify
    in the browser. `organization_id` selects the graph.
  • PER-EMPLOYEE within an org — every node carries `owner_email` (the employee whose
    mailbox it came from); dedup key is (email, owner_email). Re-syncing one mailbox
    prunes + rebuilds ONLY that owner's nodes, and reads filter by owner_email. The
    corr_count / last_contact signals are inherently per-employee, which is why a
    contact is one node *per owner* rather than one shared node.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from falkordb import FalkorDB

from app.settings import settings

logger = logging.getLogger(__name__)

_db: Optional[FalkorDB] = None
_lock = threading.Lock()
_indexed_graphs: set[str] = set()  # graph names whose indexes are ensured
_EMB_DIM = 1536  # text-embedding-3-small


def _embedder():
    from client.llm_client import get_embeddings
    return get_embeddings()


def _contact_text(c: dict) -> str:
    """The text we embed for a contact — identity + role + skills, for richer search."""
    skills = c.get("skills") or []
    parts = [
        c.get("name"),
        c.get("title"),
        c.get("seniority"),
        c.get("department"),
        c.get("company"),
        c.get("industry"),  # what the company does — the teaming-relevance signal
        c.get("domain"),
        ", ".join(skills[:10]) if skills else None,
    ]
    return " — ".join(p for p in parts if p) or (c.get("email") or "")


def _embed_query(text: str) -> Optional[list[float]]:
    if not settings.OPENAI_API_KEY:
        return None
    try:
        return _embedder().embed_query(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("embed_query failed: %s", e)
        return None


def _get_db() -> FalkorDB:
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                _db = FalkorDB(
                    host=settings.GRAPH_DATABASE_URL,
                    port=settings.GRAPH_DATABASE_PORT,
                    username=settings.GRAPH_DATABASE_USERNAME or None,
                    password=settings.GRAPH_DATABASE_PASSWORD or None,
                    ssl=settings.GRAPH_DATABASE_SSL,
                    # --- connection resilience -------------------------------------------
                    # Same class of issue as the Celery/Redis broker (see app/worker.py):
                    # Railway's proxy drops an idle TCP socket after ~15 min. A long
                    # SharePoint crawl spends several minutes doing pure Composio/Graph API
                    # calls with NO graph activity, so by the time it writes (clear_structure
                    # + upsert_structure), the pooled connection can already be dead —
                    # surfacing as "Timeout reading from socket" and silently losing the
                    # crawl's results. health_check_interval pings periodically to keep the
                    # socket warm / detect a drop before a real write depends on it;
                    # socket_keepalive + explicit timeouts make a genuinely dead connection
                    # fail FAST instead of hanging.
                    socket_keepalive=True,
                    socket_connect_timeout=10,
                    socket_timeout=30,
                    health_check_interval=30,
                )
    return _db


def _contacts_graph_name(organization_id: str) -> str:
    """Per-ORG contact graph name, e.g. `collecct_network_<org_id>` — so each org's
    network is a separate, identifiable FalkorDB graph."""
    org = re.sub(r"[^A-Za-z0-9_]", "_", (organization_id or "").strip()) or "default"
    return f"{settings.GRAPH_DATABASE_NAME}_{org}"


def get_graph(organization_id: str):
    """Return ONE org's network graph handle, ensuring indexes exist once per graph."""
    name = _contacts_graph_name(organization_id)
    g = _get_db().select_graph(name)
    if name not in _indexed_graphs:
        with _lock:
            if name not in _indexed_graphs:
                for label, prop in (
                    ("Person", "email"), ("Company", "name"),
                    ("Person", "owner_email"), ("Company", "owner_email"),
                ):
                    try:
                        g.create_node_range_index(label, prop)
                    except Exception:
                        pass  # already exists
                try:
                    g.create_node_vector_index(
                        "Person", "embedding", dim=_EMB_DIM, similarity_function="cosine"
                    )
                except Exception:
                    pass  # already exists
                _indexed_graphs.add(name)
    return g


def upsert_contacts(contacts: list[dict], owner_email: str, organization_id: str) -> int:
    """Upsert enriched contacts into ONE org's graph, for ONE owner (employee).

    `organization_id` picks the org graph; `owner_email` is the employee whose mailbox
    these came from. Dedup key is (email, owner_email) within that org graph, so the
    same external contact shared by two employees is a separate node per employee —
    keeping each employee's corr_count / network isolated. Only contacts WITH an email
    enter the graph. Returns the number written.
    """
    owner = (owner_email or "").strip().lower()
    if not owner:
        raise ValueError("upsert_contacts requires an owner_email")
    if not (organization_id or "").strip():
        raise ValueError("upsert_contacts requires an organization_id")
    rows = [
        {
            "owner_email": owner,
            # Dedup key — normalized (lowercased/trimmed) so "Foo@X.com" and
            # "foo@x.com" collapse to ONE node per owner. MERGE on (email, owner_email).
            "email": (c.get("email") or "").strip().lower(),
            "name": c.get("name"),
            "title": c.get("title"),
            "department": c.get("department"),
            "prospect_id": c.get("prospect_id"),
            "enriched": bool(c.get("enriched")),
            "company": (c.get("company") or "").strip(),
            "industry": c.get("industry"),  # what the company does (from the free domain dataset)
            "company_needs_research": bool(c.get("company_needs_research")),
            "source": c.get("source") or "outlook",
            # correspondence signal (from email history)
            "corr_count": int(c.get("count") or 0),
            "last_contact": c.get("last_seen"),
            "external": c.get("external"),
            "domain": c.get("domain"),
            # full enrichment (everything the CRM/Explorium API returned)
            "first_name": c.get("first_name"),
            "last_name": c.get("last_name"),
            "seniority": c.get("seniority"),
            "company_website": c.get("company_website"),
            "company_linkedin": c.get("company_linkedin"),
            "linkedin": c.get("linkedin"),
            "country": c.get("country"),
            "region": c.get("region"),
            "city": c.get("city"),
            "skills": c.get("skills") or [],
            "experience": c.get("experience") or [],
            "business_id": c.get("business_id"),
        }
        for c in contacts
        if (c.get("email") or "").strip()
    ]
    if not rows:
        return 0

    # Domain → company backfill: people Explorium resolved anchor a domain→company
    # mapping; same-domain contacts it missed inherit that company (e.g. Soham
    # resolves mail.composio.dev → Composio, so Sharath joins the same node).
    domain_company: dict[str, str] = {}
    for r in rows:
        if r["company"]:
            domain_company.setdefault(_norm_domain(r["domain"]), r["company"])
    for r in rows:
        if not r["company"] and r["domain"]:
            inferred = domain_company.get(_norm_domain(r["domain"]))
            if inferred:
                r["company"] = inferred

    # Embeddings for semantic search (best-effort; skipped if no OpenAI key).
    # Computed AFTER the domain backfill so the company is part of the embedded text.
    if settings.OPENAI_API_KEY:
        try:
            embs = _embedder().embed_documents([_contact_text(r) for r in rows])
            for r, e in zip(rows, embs):
                r["embedding"] = e
        except Exception as ex:  # noqa: BLE001
            logger.warning("contact embedding failed (storing without vectors): %s", ex)

    g = get_graph(organization_id)
    g.query(
        """
        UNWIND $rows AS row
        MERGE (p:Person {email: row.email, owner_email: row.owner_email})
          SET p.name = row.name,
              p.title = row.title,
              p.department = row.department,
              p.prospect_id = row.prospect_id,
              p.enriched = row.enriched,
              p.industry = row.industry,
              p.company_needs_research = row.company_needs_research,
              p.source = row.source,
              p.corr_count = row.corr_count,
              p.last_contact = row.last_contact,
              p.external = row.external,
              p.domain = row.domain,
              p.first_name = row.first_name,
              p.last_name = row.last_name,
              p.seniority = row.seniority,
              p.company_website = row.company_website,
              p.company_linkedin = row.company_linkedin,
              p.linkedin = row.linkedin,
              p.country = row.country,
              p.region = row.region,
              p.city = row.city,
              p.skills = row.skills,
              p.experience = row.experience,
              p.business_id = row.business_id
        FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END |
          SET p.embedding = vecf32(row.embedding)
        )
        FOREACH (_ IN CASE WHEN row.company = '' THEN [] ELSE [1] END |
          MERGE (c:Company {name: row.company, owner_email: row.owner_email})
          MERGE (p)-[:WORKS_AT]->(c)
        )
        """,
        params={"rows": rows},
    )
    return len(rows)


def _norm_domain(d: str | None) -> str:
    """Strip common mail subdomains so mail.composio.dev ~ composio.dev."""
    d = (d or "").lower()
    for pre in ("mail.", "email.", "smtp.", "mx.", "e.", "em."):
        if d.startswith(pre):
            d = d[len(pre):]
    return d


def update_correspondence(
    owner_email: str,
    organization_id: str,
    counts: dict[str, int],
    last_seen: dict[str, str] | None = None,
) -> int:
    """Write the relationship signal onto ONE employee's slice of the graph.

    `corr_count` and `last_contact` are inherently per-employee — Alice having emailed
    someone forty times says nothing about Bob — which is why contacts are one node per
    owner rather than one shared node.

    This exists because those two properties are currently always 0/None:
    `fetch_outlook_network` reads only the address book and has no mail-history signal,
    yet `crm_agent.py` tells the model that `corr_count` is how it should judge
    relationship strength. Until this runs, that instruction weighs a constant.

    Only updates contacts already in the graph — the sweep is a signal pass, not an
    import path, so a stranger who emailed once does not silently become a CRM contact.
    """
    owner = (owner_email or "").strip().lower()
    if not owner or not (organization_id or "").strip() or not counts:
        return 0
    seen = last_seen or {}
    rows = [
        {"email": e, "owner_email": owner, "corr_count": int(n),
         "last_contact": seen.get(e)}
        for e, n in counts.items()
        if e
    ]
    g = get_graph(organization_id)
    res = g.query(
        """
        UNWIND $rows AS row
        MATCH (p:Person {email: row.email, owner_email: row.owner_email})
        SET p.corr_count = row.corr_count,
            p.last_contact = coalesce(row.last_contact, p.last_contact)
        RETURN count(p)
        """,
        params={"rows": rows},
    )
    return int(res.result_set[0][0]) if res.result_set else 0


def update_company_for_domain(
    organization_id: str,
    domain: str,
    *,
    industry: Optional[str] = None,
    company_website: Optional[str] = None,
    company_linkedin: Optional[str] = None,
) -> int:
    """Apply a researched company's details to EVERY contact on that domain and clear the
    research flag. Returns how many contacts were updated.

    Spans every owner's slice inside the org graph: the company is the same company
    whoever happens to know the person, so one lookup improves the whole org's network.

    Re-embeds the affected contacts, which is the point rather than a nicety — `industry`
    is part of `_contact_text`, so filling it is exactly what lets the Relation agent's
    semantic search find "people at defence IT integrators" instead of only literal
    keyword matches. The domain is matched as stored (company_enrich already normalised
    it), so no second normalisation is applied here.
    """
    dom = (domain or "").strip().lower()
    if not dom or not (organization_id or "").strip():
        return 0

    g = get_graph(organization_id)
    found = g.ro_query(
        """
        MATCH (p:Person) WHERE p.domain = $dom
        OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
        RETURN p.email, p.owner_email, p.name, p.title, p.seniority,
               p.department, c.name, p.skills
        """,
        params={"dom": dom},
    )
    rows = [
        {
            "email": r[0], "owner_email": r[1], "name": r[2], "title": r[3],
            "seniority": r[4], "department": r[5], "company": r[6], "skills": r[7] or [],
            "domain": dom, "industry": industry,
            "company_website": company_website, "company_linkedin": company_linkedin,
            "embedding": None,
        }
        for r in found.result_set
    ]
    if not rows:
        return 0

    if settings.OPENAI_API_KEY:
        try:
            embs = _embedder().embed_documents([_contact_text(r) for r in rows])
            for row, emb in zip(rows, embs):
                row["embedding"] = emb
        except Exception as exc:  # noqa: BLE001 — never lose the research over an embedding blip
            logger.warning("re-embedding after company research failed: %s", exc)

    g.query(
        """
        UNWIND $rows AS row
        MATCH (p:Person {email: row.email, owner_email: row.owner_email})
        SET p.industry = coalesce(row.industry, p.industry),
            p.company_website = coalesce(row.company_website, p.company_website),
            p.company_linkedin = coalesce(row.company_linkedin, p.company_linkedin),
            p.company_needs_research = false
        FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END |
          SET p.embedding = vecf32(row.embedding)
        )
        """,
        params={"rows": rows},
    )
    return len(rows)


def clear_owner_graph(owner_email: str, organization_id: str) -> None:
    """Prune ONE employee's subgraph within their org graph — delete only nodes they own.

    This is what a re-sync uses: it wipes the acting employee's old contacts and
    companies (so stale ones drop out) without touching any other employee's network.
    """
    owner = (owner_email or "").strip().lower()
    if not owner or not (organization_id or "").strip():
        raise ValueError("clear_owner_graph requires owner_email + organization_id")
    get_graph(organization_id).query(
        "MATCH (n) WHERE n.owner_email = $owner DETACH DELETE n",
        params={"owner": owner},
    )


def clear_graph(organization_id: str) -> None:
    """Wipe ALL nodes + edges from ONE org's network graph (keeps indexes).

    Full reset across every owner in that org — use only for migration / admin.
    """
    get_graph(organization_id).query("MATCH (n) DETACH DELETE n")


def stats(organization_id: str) -> dict:
    """Quick counts for one org's graph (verification / the UI)."""
    g = get_graph(organization_id)
    people = g.ro_query("MATCH (p:Person) RETURN count(p)").result_set[0][0]
    companies = g.ro_query("MATCH (c:Company) RETURN count(c)").result_set[0][0]
    return {"people": people, "companies": companies}


def get_network(owner_email: str, organization_id: str) -> dict:
    """Return ONE employee's contact graph as { nodes, edges } for the UI to render."""
    owner = (owner_email or "").strip().lower()
    if not owner or not (organization_id or "").strip():
        return {"nodes": [], "edges": []}
    g = get_graph(organization_id)
    nodes: list[dict] = []
    edges: list[dict] = []

    for email, name, title, company, external, corr, enriched in g.ro_query(
        "MATCH (p:Person {owner_email: $owner}) "
        "RETURN p.email, p.name, p.title, p.company, p.external, p.corr_count, p.enriched",
        params={"owner": owner},
    ).result_set:
        nodes.append({
            "id": email,
            "label": name or email,
            "type": "Person",
            "email": email,
            "title": title,
            "company": company,
            "external": external,
            "enriched": enriched,
            "weight": corr or 1,
        })

    for (cname,) in g.ro_query(
        "MATCH (c:Company {owner_email: $owner}) RETURN c.name", params={"owner": owner}
    ).result_set:
        nodes.append({"id": f"company:{cname}", "label": cname, "type": "Company"})

    for email, cname in g.ro_query(
        "MATCH (p:Person {owner_email: $owner})-[:WORKS_AT]->(c:Company) RETURN p.email, c.name",
        params={"owner": owner},
    ).result_set:
        edges.append({"source": email, "target": f"company:{cname}", "type": "WORKS_AT"})

    return {"nodes": nodes, "edges": edges}


def get_contact_relationship(email: str, owner_email: str, organization_id: str) -> dict | None:
    """How well ONE employee knows one contact — the relationship signal for outreach.

    Returns the acting employee's contact frequency (corr_count) + last-seen +
    enrichment for the given email, so the Mail Agent tunes tone (warm/frequent vs.
    brand-new) on THAT employee's real history. Returns None if the person isn't in
    the employee's graph.
    """
    owner = (owner_email or "").strip().lower()
    if not email or not email.strip() or not owner or not (organization_id or "").strip():
        return None
    g = get_graph(organization_id)
    rows = g.ro_query(
        "MATCH (p:Person {owner_email: $owner}) WHERE toLower(p.email) = toLower($email) "
        "RETURN p.email, p.name, p.title, p.company, p.corr_count, p.last_contact, p.enriched "
        "LIMIT 1",
        params={"email": email.strip(), "owner": owner},
    ).result_set
    if not rows:
        return None
    email_, name, title, company, corr, last, enriched = rows[0]
    return {
        "email": email_,
        "name": name,
        "title": title,
        "company": company,
        "corr_count": int(corr or 0),
        "last_contact": last,
        "enriched": bool(enriched),
    }


def search_contacts(
    terms: list[str], owner_email: str, organization_id: str, limit: int = 25
) -> list[dict]:
    """Search ONE employee's graph for people whose company / title / domain matches a term.

    Case-insensitive CONTAINS match, ranked by contact frequency. This is what the
    CRM Agent's search tool calls — so only relevant candidates FROM THE ACTING
    EMPLOYEE'S NETWORK enter its context, instead of dumping the whole network.
    """
    owner = (owner_email or "").strip().lower()
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms or not owner or not (organization_id or "").strip():
        return []
    g = get_graph(organization_id)
    res = g.ro_query(
        """
        UNWIND $terms AS term
        MATCH (p:Person {owner_email: $owner})
        OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
        WITH p, c, term
        WHERE toLower(coalesce(c.name, '')) CONTAINS term
           OR toLower(coalesce(p.title, '')) CONTAINS term
           OR toLower(coalesce(p.domain, '')) CONTAINS term
        RETURN DISTINCT p.email, p.name, p.title, c.name, p.corr_count
        ORDER BY p.corr_count DESC
        LIMIT $limit
        """,
        params={"terms": terms, "limit": limit, "owner": owner},
    )
    return [
        {"email": r[0], "name": r[1], "title": r[2], "company": r[3], "corr_count": r[4] or 0}
        for r in res.result_set
    ]


def semantic_search_contacts(
    query: str, owner_email: str, organization_id: str, k: int = 15
) -> list[dict]:
    """Vector (semantic) search over ONE employee's contact embeddings — finds people by
    *meaning*, not just literal keyword overlap. Filter-then-rank: only the acting
    employee's nodes are scored (FalkorDB's vector index can't filter inline), so results
    never leak across employees. Returns [] if embeddings/OpenAI aren't available.
    """
    owner = (owner_email or "").strip().lower()
    if not owner or not (organization_id or "").strip():
        return []
    vec = _embed_query(query)
    if vec is None:
        return []
    g = get_graph(organization_id)
    try:
        res = g.ro_query(
            """
            MATCH (p:Person {owner_email: $owner})
            WHERE p.embedding IS NOT NULL
            OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
            RETURN p.email, p.name, p.title, c.name, p.corr_count,
                   vec.cosineDistance(p.embedding, vecf32($vec)) AS dist
            ORDER BY dist ASC
            LIMIT $k
            """,
            params={"k": k, "vec": vec, "owner": owner},
        )
    except Exception as e:  # noqa: BLE001 — no embeddings yet / function unavailable
        logger.warning("vector search failed: %s", e)
        return []
    return [
        {"email": r[0], "name": r[1], "title": r[2], "company": r[3],
         "corr_count": r[4] or 0, "score": r[5]}
        for r in res.result_set
    ]


def search_contacts_hybrid(
    query: str, owner_email: str, organization_id: str,
    terms: list[str] | None = None, k: int = 15,
) -> list[dict]:
    """Keyword (CONTAINS) + semantic (vector) search over ONE employee's network,
    merged + deduped by email."""
    merged: dict[str, dict] = {}
    for c in search_contacts(terms or query.split(), owner_email, organization_id, limit=k):
        merged[c["email"]] = c
    for c in semantic_search_contacts(query, owner_email, organization_id, k=k):
        merged.setdefault(c["email"], c)  # keyword hit wins on dup
    return list(merged.values())


def candidate_contacts(owner_email: str, organization_id: str, limit: int = 200) -> list[dict]:
    """Pull ONE employee's candidate contacts (+ their company) for the CRM Agent to rank."""
    owner = (owner_email or "").strip().lower()
    if not owner or not (organization_id or "").strip():
        return []
    g = get_graph(organization_id)
    res = g.ro_query(
        """
        MATCH (p:Person {owner_email: $owner})
        OPTIONAL MATCH (p)-[:WORKS_AT]->(c:Company)
        RETURN p.name, p.email, p.title, p.department, c.name, p.corr_count, p.last_contact
        ORDER BY p.corr_count DESC
        LIMIT $limit
        """,
        params={"limit": limit, "owner": owner},
    )
    out = []
    for name, email, title, dept, company, corr, last in res.result_set:
        out.append({
            "name": name, "email": email, "title": title, "department": dept,
            "company": company, "corr_count": corr or 0, "last_contact": last,
        })
    return out
