"""Write the bid package: one background task per document, so one failure can't lose the rest."""
import logging
import os
import re
from datetime import datetime, timezone

from app.worker import celery_app
from client.crm_store import get_crm_store
from client.requirements_store import get_requirements_store

logger = logging.getLogger(__name__)

QUEUED, WRITING, WRITTEN, FAILED = "queued", "writing", "written", "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def writable(row: dict) -> list[dict]:
    return [i for i in row.get("items") or [] if i.get("production", "write") == "write"]


def _filename(title: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9 ._()-]+", " ", title)
    return (" ".join(clean.split())[:80] or "document") + ".docx"


# "All files shall be titled, '<Company Name> CIT Network Services SSN'": the name the government
# files submissions under. The company-name slot comes in many spellings.
_NAME_SLOT = r"[<\[{(]?\s*(?:company|offeror|vendor|contractor|firm|business)(?:'s)?\s+name\s*[>\]})]?"
_TITLE_RULE = re.compile(r"\b(?:titled|named|file\s?names?)\b[^\"“”'‘’]{0,40}[\"“'‘]([^\"”’]*"
                         + _NAME_SLOT + r"[^\"”’]*)[\"”’]", re.I)


def title_rule(row: dict) -> str | None:
    """The file title the solicitation prescribes, with its company-name slot, if it sets one."""
    m = _TITLE_RULE.search((row.get("submission") or {}).get("file_rules") or "")
    return re.sub(r"\.(?:pdf|docx?|xlsx?)$", "", m.group(1).strip(), flags=re.I) if m else None


def required_name(row: dict, item: dict, company: str | None) -> str | None:
    """That title with the company's legal name in it, or None. Several documents to write each
    get their own title after it, so none replaces another."""
    rule = title_rule(row)
    if not rule or not company:
        return None
    name = re.sub(_NAME_SLOT, lambda _: company, rule, count=1, flags=re.I)
    if len(writable(row)) > 1:
        name = f"{name} - {item.get('title')}"
    return _filename(name)


def free_name(row: dict, item_id: str, name: str) -> str:
    """SharePoint replaces a same-named file, so two checklist lines must never share a name."""
    used = {f["title"] for i in row.get("items") or [] if i["id"] != item_id
            for f in i.get("files") or []}
    base, ext = os.path.splitext(name)
    candidate, n = name, 2
    while candidate in used:
        candidate, n = f"{base} ({n}){ext}", n + 1
    return candidate


_VALIDITY = re.compile(r"(?:valid|acceptance period)[^.]{0,60}?\b(\d{2,3})\s*(?:calendar\s*)?days", re.I)


def _cover_meta(opp: dict, row: dict, card: dict, employee_email: str | None, source: str,
                item: dict) -> dict:
    """What the cover, header and footer say. The contact is whoever writes or approved the bid;
    the validity period only when the solicitation sets one."""
    from utils.organizations import user_display_name

    email = employee_email or next((e for e in [row.get("approved_by")] if e and "@" in e), None)
    poc = None
    if email:
        name = user_display_name(email)
        poc = " | ".join(x for x in (name, "[NEEDS: phone]", email) if x)
    m = _VALIDITY.search(source or "")
    return {
        "company": card, "bid_title": opp.get("title"), "agency": opp.get("agency"),
        "volume": item.get("title"), "kind": item.get("kind"),
        "vehicle": opp.get("contract_vehicle"), "poc": poc,
        "validity": f"This proposal is valid for {m.group(1)} calendar days from submission." if m else None,
    }


def queue_package(opportunity_id: str, organization_id: str, employee_email: str | None = None,
                  only_unwritten: bool = True) -> int:
    """Queue every document Collecct can write. Returns how many."""
    store = get_requirements_store()
    row = store.get(organization_id, opportunity_id) or {}
    queued = 0
    for item in writable(row):
        if only_unwritten and item.get("gen_status") == WRITTEN:
            continue
        queue_item(opportunity_id, organization_id, item["id"], employee_email)
        queued += 1
    return queued


def queue_item(opportunity_id: str, organization_id: str, item_id: str,
               employee_email: str | None = None) -> None:
    get_requirements_store().edit_item(organization_id, opportunity_id, item_id,
                                       {"gen_status": QUEUED, "gen_error": None, "gen_at": _now()})
    write_item_task.delay(opportunity_id, organization_id, item_id, employee_email)


@celery_app.task(bind=True, name="proposal.write_item", max_retries=1, default_retry_delay=20)
def write_item_task(self, opportunity_id: str, organization_id: str, item_id: str,
                    employee_email: str | None = None) -> dict:
    from agent.company_profile import company_card
    from agent.proposal_grounding import ground
    from agent.proposal_writer import (_page_budget, document_text, notes_text, unverified_numbers,
                                       write_document, writer_session_id)
    from utils import bid_files
    from utils.docx_writer import estimate_pages, front_matter, render
    from utils.rewrite_pass import rewrite_document

    store, crm = get_requirements_store(), get_crm_store()
    row = store.get(organization_id, opportunity_id)
    item = next((i for i in (row or {}).get("items") or [] if i["id"] == item_id), None)
    opp = crm.get_opportunity(opportunity_id, organization_id)
    if item is None or opp is None:
        return {"item_id": item_id, "skipped": "not found"}

    # `gen_at` is the heartbeat the checklist reads to tell a slow write from a dead one
    # (requirements_store.expire_stuck): stamped as each long stage starts.
    beat = lambda: store.edit_item(organization_id, opportunity_id, item_id, {"gen_at": _now()})  # noqa: E731
    store.edit_item(organization_id, opportunity_id, item_id, {"gen_status": WRITING, "gen_at": _now()})
    try:
        bid_files.bid_folder(organization_id, opportunity_id)  # before spending a model call
        source = store.source_text(organization_id, opportunity_id)
        # A rewrite picks up from this document's last draft (the writer's own session) and the
        # problems found in it; what the person told the writer counts as a source.
        rewrite = any(f.get("generated") for f in item.get("files") or [])
        doc = write_document(organization_id, opp, row, item, source, employee_email,
                             session_id=writer_session_id(opportunity_id, item_id), rewrite=rewrite)
        beat()
        card = company_card(organization_id)
        notes = notes_text(item.get("writer_notes"))
        # Facts from the library, claims checked against their own files, notes flagged.
        gaps = list(doc.gaps) + ground(doc, organization_id, employee_email, source,
                                       card["name"] or "", notes)
        # The writer's prose, reworded by a model that adds no watermark. After grounding (the
        # markers are gone, the facts are in) and before the number check, which so also covers
        # the new wording.
        beat()
        gaps += rewrite_document(doc)
        beat()
        unverified = unverified_numbers(doc, organization_id, f"{source}\n{notes}")
        if unverified:
            gaps.append("Check these figures and numbers, which aren't in your documents: " + ", ".join(unverified))
        content = render(doc, opp.get("solicitation_number"), row.get("format_rules"),
                         _cover_meta(opp, row, card, employee_email, source, item), item.get("page_limit"))

        # Only a SAM.gov legal name goes in a prescribed title: without a UEI the card's name is
        # the Collecct org name, which the government must never see as the company.
        legal = card.get("name") if card.get("uei") else None
        prescribed = required_name(row, item, legal)
        if title_rule(row) and not prescribed:
            gaps.append(f'Rename the file to the solicitation\'s required title: "{title_rule(row)}". '
                        "Add your UEI in Organisation settings and Collecct will name it for you.")
        name = free_name(row, item_id, prescribed or _filename(item.get("title") or doc.title))
        saved = bid_files.save(organization_id, opportunity_id, name, content,
                               bid_files.subfolder_for(item.get("title")))
        # One current draft per item. What a person attached stays alongside it.
        files = item.get("files") or []
        kept = [f for f in files if not f.get("generated")]
        previous = next((f["id"] for f in files if f.get("generated")), None)
        doc_id = bid_files.record(opportunity_id, "proposal_writer", "proposal", name, saved, previous)
        words = len(document_text(doc).split())
        limit, _ = _page_budget(item.get("page_limit"))
        # Measured on the file itself (tables and headings take room words don't show); the cover
        # is left out only where the solicitation says it doesn't count.
        cover, _ = front_matter(item.get("page_limit"), (row.get("format_rules") or {}).get("page_counting"))
        pages = estimate_pages(content, skip_cover=cover)
        if limit and pages > limit:
            gaps.append(f"About {pages} pages against a {limit}-page limit — needs trimming.")
        store.edit_item(organization_id, opportunity_id, item_id, {
            "files": [*kept, {"id": doc_id, "title": name, "size": len(content), "generated": True,
                              "edit_url": saved["edit_url"]}],
            "gen_status": WRITTEN, "gen_error": None, "gaps": gaps, "pages": pages,
        })
        logger.info("Proposal: wrote %s (%d words, %d gap(s))", name, words, len(gaps))
        return {"item_id": item_id, "status": WRITTEN, "gaps": len(gaps)}
    except bid_files.NoSharePoint as exc:  # a retry can't fix this; a person has to
        store.edit_item(organization_id, opportunity_id, item_id,
                        {"gen_status": FAILED, "gen_error": str(exc)})
        return {"item_id": item_id, "status": FAILED}
    except Exception as exc:  # noqa: BLE001
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        logger.warning("Proposal: couldn't write %s: %s", item.get("title"), exc)
        store.edit_item(organization_id, opportunity_id, item_id,
                        {"gen_status": FAILED, "gen_error": str(exc)[:300]})
        return {"item_id": item_id, "status": FAILED}
