"""The library's CONTENT layer in each org's SharePoint knowledge graph.

The structure graph (client/sharepoint_graph.py) knows where every file is and who can read
it. This module adds what is IN the files, from the Library agent's analysis:

  (:SPNode {type:'file'})  gains  category, summary, confidence, analysis_status
                           and its embedding is re-pointed at the summary (search by meaning,
                           not by file name).
  (:LibChunk {id, item_id, idx, text, category, embedding})  — searchable passages.
  (:Contract {key, ...}) (:Person {key, ...}) (:Agency {key, name})
  (file)-[:DESCRIBES]->(:Contract)     a past-performance / CPARS / proposal file
  (file)-[:RESUME_OF]->(:Person)
  (file)-[:SUBMITTED_TO]->(:Agency)    a past proposal
  (:Contract)-[:FOR_AGENCY]->(:Agency)

SURVIVING THE REBUILD. Every sync deletes and recreates the SPNodes. Chunks and entities are
separate labels, so they survive; the file -> entity EDGES do not. `relink()` restores the
SPNode properties and edges from Mongo (client/library_store.py) after each rebuild — no model
calls, so a nightly sync never re-pays for analysis.

ACCESS. Chunks and entities carry no roster of their own: every read joins back to the file's
SPNode and applies the same `_ACCESS` predicate, so a passage or a contract is visible exactly
when the file it came from is.
"""
from __future__ import annotations

import logging
import re

from app.settings import settings
from client.graph_store import _embed_query, _embedder
from client.sharepoint_graph import _ACCESS, _EMB_DIM, _graph, _sp_graph_name
from models.library import CATEGORY_LABELS

logger = logging.getLogger(__name__)

_indexed: set[str] = set()
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_CHUNKS = 40


def _g(organization_id: str):
    g = _graph(organization_id)
    name = _sp_graph_name(organization_id)
    if name not in _indexed:
        for label, prop in (("LibChunk", "item_id"), ("LibChunk", "id"), ("Contract", "key"),
                            ("Person", "key"), ("Agency", "key")):
            try:
                g.create_node_range_index(label, prop)
            except Exception:
                pass
        try:
            g.create_node_vector_index("LibChunk", "embedding", dim=_EMB_DIM, similarity_function="cosine")
        except Exception:
            pass
        _indexed.add(name)
    return g


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def summary_text(row: dict) -> str:
    """What we embed for an analysed file — its kind and what it says, plus where it lives."""
    label = CATEGORY_LABELS.get(row.get("category") or "", "Document")
    return f"{label}: {row.get('name')} | {row.get('path')} | {row.get('summary') or ''}"


def chunk_text(text: str) -> list[str]:
    """Overlapping passages, cut on paragraph breaks where possible."""
    text = (text or "")[: settings.LIBRARY_READ_CHARS]
    out: list[str] = []
    i = 0
    while i < len(text) and len(out) < MAX_CHUNKS:
        end = min(len(text), i + CHUNK_CHARS)
        if end < len(text):
            cut = text.rfind("\n\n", i + CHUNK_CHARS // 2, end)
            end = cut if cut > 0 else end
        piece = text[i:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        i = max(end - CHUNK_OVERLAP, i + 1)
    return out


# ---------------------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------------------
def _entity_rows(row: dict) -> dict:
    """The Contract / Person / Agency nodes and file edges one analysed row implies."""
    contracts, people, agencies, submitted = [], [], [], []
    for c in row.get("contracts") or []:
        # No contract number -> customer + title, and only when BOTH are known: a bare title
        # ("Help Desk Support") would merge unrelated contracts into one node.
        key = _norm(c.get("contract_number")) or (
            _norm(f"{c.get('customer')} {c.get('title')}") if c.get("customer") and c.get("title") else ""
        )
        if not key:
            continue
        agency = c.get("agency") or c.get("customer")
        contracts.append({
            "key": f"contract:{key}", "title": c.get("title"), "customer": c.get("customer"),
            "agency": c.get("agency"), "contract_number": c.get("contract_number"),
            "value_usd": c.get("value_usd"), "start_date": c.get("start_date"),
            "end_date": c.get("end_date"), "naics": c.get("naics"), "rating": c.get("rating"),
            "agency_key": f"agency:{_norm(agency)}" if _norm(agency) else None,
        })
        if _norm(agency):
            agencies.append({"key": f"agency:{_norm(agency)}", "name": agency})
    for p in row.get("people") or []:
        if _norm(p.get("name")):
            people.append({
                "key": f"person:{_norm(p.get('name'))}", "name": p.get("name"),
                "current_role": p.get("current_role"), "clearance": p.get("clearance"),
                "years_experience": p.get("years_experience"),
            })
    prop = row.get("proposal") or {}
    if _norm(prop.get("agency")):
        a = {"key": f"agency:{_norm(prop.get('agency'))}", "name": prop.get("agency")}
        agencies.append(a)
        submitted.append(a["key"])
    return {"contracts": contracts, "people": people, "agencies": agencies, "submitted": submitted}


def _link_entities(g, item_id: str, ents: dict) -> None:
    """MERGE the entities and this file's edges to them. Properties only ever fill in — a
    second document about the same contract never blanks what the first one established."""
    if ents["agencies"]:
        g.query(
            "UNWIND $rows AS r MERGE (a:Agency {key:r.key}) SET a.name = coalesce(a.name, r.name)",
            params={"rows": ents["agencies"]},
        )
    if ents["contracts"]:
        g.query(
            """
            UNWIND $rows AS r
            MERGE (c:Contract {key: r.key})
              SET c.title = coalesce(c.title, r.title), c.customer = coalesce(c.customer, r.customer),
                  c.agency = coalesce(c.agency, r.agency),
                  c.contract_number = coalesce(c.contract_number, r.contract_number),
                  c.value_usd = coalesce(c.value_usd, r.value_usd),
                  c.start_date = coalesce(c.start_date, r.start_date),
                  c.end_date = coalesce(c.end_date, r.end_date), c.naics = coalesce(c.naics, r.naics),
                  c.rating = coalesce(r.rating, c.rating)
            WITH c, r
            MATCH (f:SPNode {id: $item})
            MERGE (f)-[:DESCRIBES]->(c)
            WITH c, r WHERE r.agency_key IS NOT NULL
            MATCH (a:Agency {key: r.agency_key})
            MERGE (c)-[:FOR_AGENCY]->(a)
            """,
            params={"rows": ents["contracts"], "item": item_id},
        )
    if ents["people"]:
        g.query(
            """
            UNWIND $rows AS r
            MERGE (p:Person {key: r.key})
              SET p.name = coalesce(p.name, r.name), p.current_role = coalesce(r.current_role, p.current_role),
                  p.clearance = coalesce(r.clearance, p.clearance),
                  p.years_experience = coalesce(r.years_experience, p.years_experience)
            WITH p
            MATCH (f:SPNode {id: $item})
            MERGE (f)-[:RESUME_OF]->(p)
            """,
            params={"rows": ents["people"], "item": item_id},
        )
    if ents["submitted"]:
        g.query(
            "UNWIND $keys AS k MATCH (a:Agency {key:k}), (f:SPNode {id:$item}) MERGE (f)-[:SUBMITTED_TO]->(a)",
            params={"keys": ents["submitted"], "item": item_id},
        )


def write_document(organization_id: str, row: dict, text: str | None) -> int:
    """Mirror ONE freshly analysed file into the graph. `row` is its Mongo row (with
    `summary_embedding`); `text` is the parsed document, chunked into passages. Returns the
    number of passages written."""
    g = _g(organization_id)
    item = row["item_id"]
    g.query(
        """
        MATCH (f:SPNode {id: $id})
        SET f.category = $category, f.summary = $summary, f.confidence = $confidence,
            f.analysis_status = $status
        """,
        params={"id": item, "category": row.get("category"), "summary": row.get("summary"),
                "confidence": row.get("confidence"), "status": row.get("status")},
    )
    g.query("MATCH (f:SPNode {id:$id})-[r:DESCRIBES|RESUME_OF|SUBMITTED_TO]->() DELETE r", params={"id": item})
    if row.get("summary_embedding"):
        g.query("MATCH (f:SPNode {id:$id}) SET f.embedding = vecf32($e)",
                params={"id": item, "e": row["summary_embedding"]})
    _link_entities(g, item, _entity_rows(row))

    g.query("MATCH (c:LibChunk {item_id:$id}) DELETE c", params={"id": item})
    chunks = chunk_text(text or "")
    if not chunks or not settings.embeddings_enabled:
        return 0
    try:
        embs = _embedder().embed_documents(chunks)
    except Exception as exc:  # noqa: BLE001 — passages are a bonus; the analysis already stands
        logger.warning("Library chunk embedding failed for %s: %s", row.get("name"), exc)
        return 0
    g.query(
        """
        UNWIND $rows AS r
        CREATE (c:LibChunk {id: r.id, item_id: $item, idx: r.idx, text: r.text, category: $category})
        SET c.embedding = vecf32(r.embedding)
        """,
        params={
            "item": item, "category": row.get("category"),
            "rows": [{"id": f"{item}#{i}", "idx": i, "text": t, "embedding": e}
                     for i, (t, e) in enumerate(zip(chunks, embs))],
        },
    )
    return len(chunks)


def relink(organization_id: str, rows: list[dict]) -> None:
    """After a structure rebuild: put every analysed file's properties, summary embedding and
    entity edges back, then drop passages and entities whose file is gone. No model calls."""
    g = _g(organization_id)
    if rows:
        g.query(
            """
            UNWIND $rows AS r
            MATCH (f:SPNode {id: r.id})
            SET f.category = r.category, f.summary = r.summary, f.confidence = r.confidence,
                f.analysis_status = r.status
            """,
            params={"rows": [
                {"id": r["item_id"], "category": r.get("category"), "summary": r.get("summary"),
                 "confidence": r.get("confidence"), "status": r.get("status")}
                for r in rows
            ]},
        )
        embedded = [{"id": r["item_id"], "e": r["summary_embedding"]} for r in rows if r.get("summary_embedding")]
        for i in range(0, len(embedded), 200):
            g.query("UNWIND $rows AS r MATCH (f:SPNode {id:r.id}) SET f.embedding = vecf32(r.e)",
                    params={"rows": embedded[i:i + 200]})
        g.query(
            "UNWIND $rows AS r MATCH (c:LibChunk {item_id:r.id}) SET c.category = r.category",
            params={"rows": [{"id": r["item_id"], "category": r.get("category")} for r in rows]},
        )
        for r in rows:
            try:
                _link_entities(g, r["item_id"], _entity_rows(r))
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the rest
                logger.warning("Library relink failed for %s: %s", r.get("name"), exc)
    prune(organization_id)


def remove_items(organization_id: str, item_ids: list[str]) -> None:
    if item_ids:
        _g(organization_id).query("MATCH (c:LibChunk) WHERE c.item_id IN $ids DELETE c",
                                  params={"ids": item_ids})
    prune(organization_id)


def prune(organization_id: str) -> None:
    """Delete passages whose file is gone, then entities no file points at any more."""
    g = _g(organization_id)
    g.query(
        "MATCH (c:LibChunk) OPTIONAL MATCH (f:SPNode {id: c.item_id}) WITH c, f WHERE f IS NULL DELETE c"
    )
    g.query("MATCH (e) WHERE (e:Contract OR e:Person) AND NOT ()-[:DESCRIBES|RESUME_OF]->(e) DETACH DELETE e")
    g.query("MATCH (a:Agency) WHERE NOT ()--(a) DELETE a")


# ---------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------
def search_passages(query: str, organization_id: str, employee_email: str | None = None,
                    categories: list[str] | None = None, k: int = 8) -> list[dict]:
    """Passages from the library closest in MEANING to `query`, ACL-filtered through each
    passage's file. Optionally limited to some categories."""
    vec = _embed_query(query)
    if vec is None:
        return []
    g = _g(organization_id)
    where = ["c.embedding IS NOT NULL"]
    if categories:
        where.append("c.category IN $cats")
    if employee_email:
        where.append(_ACCESS)
    try:
        res = g.ro_query(
            f"""
            MATCH (c:LibChunk)
            MATCH (n:SPNode {{id: c.item_id}})
            WHERE {' AND '.join(where)}
            RETURN c.text, c.category, n.id, n.name, n.path, n.web_url, n.drive_id,
                   vec.cosineDistance(c.embedding, vecf32($vec)) AS dist
            ORDER BY dist ASC
            LIMIT $k
            """,
            params={"vec": vec, "k": k, "cats": categories or [],
                    "email": (employee_email or "").lower()},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Library passage search failed: %s", exc)
        return []
    return [
        {"text": r[0], "category": r[1], "id": r[2], "name": r[3], "path": r[4],
         "web_url": r[5], "drive_id": r[6], "score": round(1 - float(r[7]), 3)}
        for r in res.result_set
    ]
