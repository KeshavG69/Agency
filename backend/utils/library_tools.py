"""Agent tools over the ANALYSED library — find by what documents say, not what they're named.

`search_sharepoint` (client/sharepoint_graph.py) finds files by name and path. These three
read the Library agent's analysis instead:

  find_past_performance  -> contracts we performed, with value / agency / dates / POC
  find_resumes           -> people, with role / clearance / certifications
  search_library         -> passages from inside documents, closest in meaning

Every fact returned carries the quote it was read from and a link to the source file, so the
agent can cite it. The organisation and the acting employee are BOUND here, never set by the
model, and every result is limited to files that employee may open in SharePoint.
"""
from __future__ import annotations

import json
import math
from typing import Callable

from client.graph_store import _embed_query
from client.library_store import get_library_store

_PP_CATEGORIES = ("active_contract", "past_performance", "cpars", "past_proposal")

# Files Collecct itself saved into SharePoint (drafts, matrices) get crawled into the library like
# anything else. They are never proof of work the company did: citing one would be the AI quoting
# itself. Cached briefly — a run makes many searches.
_GENERATED: tuple[float, set[str]] = (0.0, set())


def generated_item_ids() -> set[str]:
    global _GENERATED
    import time

    if time.time() - _GENERATED[0] > 120:
        from client.crm_store import get_crm_store

        ids = {d["sharepoint_item_id"] for d in get_crm_store().documents.find(
            {"sharepoint_item_id": {"$nin": [None, ""]}}, {"sharepoint_item_id": 1})}
        _GENERATED = (time.time(), ids)
    return _GENERATED[1]


def _ours(rows: list[dict]) -> list[dict]:
    skip = generated_item_ids()
    return [r for r in rows if r.get("item_id") not in skip]


def _cos(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _source(row: dict) -> dict:
    return {"file": row.get("name"), "path": row.get("path"), "web_url": row.get("web_url"),
            "id": row.get("item_id"), "drive_id": row.get("drive_id"), "category": row.get("category")}


def _items(rows: list[dict], kind: str, query: str, keep) -> list[dict]:
    """Every contract (or person) across `rows` that passes `keep`, each scored on ITS OWN
    vector — so the cloud architect in a two-person resume book outranks the PM for a cloud
    query. Falls back to the file's summary vector for rows analysed before item vectors."""
    vec = _embed_query(query) if query.strip() else None
    scored = []
    for row in rows:
        own = (row.get("item_embeddings") or {}).get(kind) or []
        for i, item in enumerate(row.get(kind) or []):
            if not keep(item):
                continue
            score = _cos(vec, own[i] if i < len(own) else row.get("summary_embedding")) if vec else 0.0
            scored.append((score, {**{k: v for k, v in item.items() if v not in (None, "", [])},
                                   "source": _source(row)}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


def find_past_performance(query: str, organization_id: str, employee_email: str | None = None,
                          agency: str | None = None, min_value_usd: float | None = None,
                          limit: int = 8) -> list[dict]:
    rows = _ours(get_library_store().analysed(organization_id, email=employee_email,
                                              categories=_PP_CATEGORIES, with_embedding=True))
    needle = (agency or "").strip().lower()

    def keep(c: dict) -> bool:
        if needle and needle not in f"{c.get('agency') or ''} {c.get('customer') or ''}".lower():
            return False
        return not (min_value_usd and (c.get("value_usd") or 0) < min_value_usd)

    return _items(rows, "contracts", query, keep)[:limit]


def find_resumes(query: str, organization_id: str, employee_email: str | None = None,
                 clearance: str | None = None, limit: int = 8) -> list[dict]:
    rows = _ours(get_library_store().analysed(organization_id, email=employee_email,
                                              categories=("resume",), with_embedding=True))
    needle = (clearance or "").strip().lower()
    return _items(rows, "people", query,
                  lambda p: not needle or needle in (p.get("clearance") or "").lower())[:limit]


def library_tools(organization_id: str | None, employee_email: str | None) -> list[Callable]:
    """The three tools, bound to one org + one employee, ready to hand to an Agno agent."""
    org = organization_id or ""

    def find_past_performance_tool(query: str, agency: str = "", min_value_usd: float = 0) -> str:
        """Find contracts our company has PERFORMED that match `query` (e.g. "cloud migration
        for DoD", "help desk support"). Optional filters: `agency` (matches agency or customer
        name, e.g. "Army") and `min_value_usd`. Each result has the contract's title, customer,
        agency, contract number, value, dates, scope and customer POC where the document states
        them, the `evidence` quote each fact came from, and its source file. Cite these — never
        describe past performance that is not in a result. Returns [] if nothing matches."""
        return json.dumps(find_past_performance(query, org, employee_email, agency or None,
                                                min_value_usd or None), default=str)

    def find_resumes_tool(query: str, clearance: str = "") -> str:
        """Find PEOPLE on our team from their resumes that match `query` (e.g. "program manager
        with PMP", "cloud architect AWS"). Optional `clearance` filter (e.g. "Secret", "Top
        Secret"). Each result has name, role, years of experience, clearance, certifications,
        education, skills, the evidence quotes and the source file. Returns [] if nothing matches."""
        return json.dumps(find_resumes(query, org, employee_email, clearance or None), default=str)

    def search_library_tool(query: str, category: str = "") -> str:
        """Search INSIDE the company's documents for passages about `query` — methodology,
        approach, tools, results. Optional `category` limits it to one kind of document:
        active_contract, past_performance, cpars, resume, past_proposal, capture_plan, capability_statement,
        technical_approach, management_plan, pricing, certification, contract_legal,
        solicitation, other.
        Returns passages with their source file. Returns [] if nothing relevant."""
        from client.library_graph import search_passages

        skip = generated_item_ids()
        found = search_passages(query, org, employee_email, [category] if category else None)
        return json.dumps([p for p in found if p.get("id") not in skip], default=str)

    return [find_past_performance_tool, find_resumes_tool, search_library_tool]


LIBRARY_TOOLS_GUIDE = """\
LIBRARY TOOLS (search by what documents SAY — prefer these for facts):
- `find_past_performance_tool(query, agency?, min_value_usd?)` — contracts we performed, with
  value, agency, dates and the quote each fact came from.
- `find_resumes_tool(query, clearance?)` — our people, from their resumes.
- `search_library_tool(query, category?)` — passages from inside our documents.
Use `search_sharepoint` when you need to find a file or folder by name."""
