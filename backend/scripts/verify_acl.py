"""Audit the stored SharePoint ACL rosters — is the ACL actually correct?

Run:  uv run --project backend python backend/scripts/verify_acl.py
Read-only. Prints (1) roster-size distribution, (2) org_wide split, (3) real
sample rosters with emails, (4) empty-roster breakdown by site, (5) a live
ground-truth re-fetch of a few files' /permissions to compare stored vs reality.
"""
import os
import sys

# settings reads a relative .env — run from the backend dir regardless of cwd.
BACKEND = "/Users/keshav/Developer/Others/AI-Agency/backend"
os.chdir(BACKEND)
sys.path.insert(0, BACKEND)


def q(g, cypher, **params):
    return g.ro_query(cypher, params=params).result_set


def main():
    from client.sharepoint_graph import _graph

    # Graphs are now per-org (sharepoint_structure_<org>). Set VERIFY_ORG to the org id.
    g = _graph(os.getenv("VERIFY_ORG", ""))

    print("=" * 70)
    print("1) ROSTER-SIZE DISTRIBUTION (files) — is everything really '1 item'?")
    print("=" * 70)
    rows = q(g, """
        MATCH (n:SPNode {type:'file'})
        RETURN size(coalesce(n.permitted_emails,[])) AS rs, count(*) AS cnt
        ORDER BY rs
    """)
    total_files = sum(r[1] for r in rows)
    for rs, cnt in rows:
        print(f"   roster_size={rs:>3}  ->  {cnt:>4} files")
    print(f"   TOTAL files: {total_files}")

    print()
    print("=" * 70)
    print("2) ORG_WIDE / ACCESSIBLE-TO-NOBODY SPLIT (files)")
    print("=" * 70)
    ow = q(g, """
        MATCH (n:SPNode {type:'file'})
        RETURN coalesce(n.org_wide,false) AS ow, count(*) AS cnt
    """)
    for v, cnt in ow:
        print(f"   org_wide={str(v):>5}  ->  {cnt:>4} files")
    nobody = q(g, """
        MATCH (n:SPNode {type:'file'})
        WHERE size(coalesce(n.permitted_emails,[]))=0 AND coalesce(n.org_wide,false)=false
        RETURN count(*)
    """)[0][0]
    print(f"   ACCESSIBLE TO NOBODY (empty roster + not org_wide): {nobody} files")

    print()
    print("=" * 70)
    print("3) SAMPLE REAL ROSTERS (biggest first) — do the emails look real?")
    print("=" * 70)
    rows = q(g, """
        MATCH (n:SPNode {type:'file'})
        WHERE size(coalesce(n.permitted_emails,[]))>0
        RETURN n.path, coalesce(n.org_wide,false), n.permitted_emails
        ORDER BY size(n.permitted_emails) DESC
        LIMIT 20
    """)
    for path, owv, emails in rows:
        print(f"   [{len(emails)}] org_wide={owv}  {path}")
        print(f"        {emails}")

    print()
    print("=" * 70)
    print("4) DISTINCT PEOPLE RESOLVED ACROSS ALL ROSTERS")
    print("=" * 70)
    people = q(g, """
        MATCH (n:SPNode) WHERE size(coalesce(n.permitted_emails,[]))>0
        UNWIND n.permitted_emails AS e
        RETURN DISTINCT e ORDER BY e
    """)
    print(f"   {len(people)} distinct emails:")
    for (e,) in people:
        print(f"      {e}")

    print()
    print("=" * 70)
    print("5) EMPTY-ROSTER FILES BY SITE (top path segment)")
    print("=" * 70)
    rows = q(g, """
        MATCH (n:SPNode {type:'file'})
        WHERE size(coalesce(n.permitted_emails,[]))=0 AND coalesce(n.org_wide,false)=false
        RETURN split(n.path,'/')[0] AS site, count(*) AS cnt
        ORDER BY cnt DESC
    """)
    for site, cnt in rows:
        print(f"   {cnt:>4}  {site}")

    print()
    print("=" * 70)
    print("6) LIVE GROUND-TRUTH — re-fetch /permissions for 3 files, compare")
    print("=" * 70)
    try:
        from utils.sharepoint_graph_client import (
            roster_for_item, graph_account, sp_rest_account, _site_base,
        )
        acc = graph_account()
        sp = sp_rest_account()
        # pick files that DO have a stored roster (so there is something to compare)
        sample = q(g, """
            MATCH (n:SPNode {type:'file'})
            WHERE size(coalesce(n.permitted_emails,[]))>0
            RETURN n.id, n.drive_id, n.web_url, n.path, n.permitted_emails, coalesce(n.org_wide,false)
            LIMIT 3
        """)
        for item_id, drive_id, web_url, path, stored, stored_ow in sample:
            base = _site_base(web_url)
            live = roster_for_item(drive_id, item_id, acc, base, sp)
            live_emails = sorted(live["permitted_emails"])
            stored_s = sorted(stored)
            match = "MATCH ✅" if live_emails == stored_s and bool(live["org_wide"]) == bool(stored_ow) else "DIFF ❌"
            print(f"   {match}  {path}")
            print(f"        stored: ow={stored_ow} {stored_s}")
            print(f"        live  : ow={live['org_wide']} {live_emails}")
    except Exception as exc:  # noqa: BLE001
        print(f"   (live check skipped: {exc})")


if __name__ == "__main__":
    main()
