"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type GraphNode } from "@/lib/data";
import { contactGraphQuery } from "@/lib/queries";
import { useUiStore } from "@/lib/stores/uiStore";
import SuggestionsReview from "./SuggestionsReview";

// The force-directed graph view was removed: at ~3,000 contacts its physics loop pinned the
// browser. The List and By-company views (kept below) render the same data as fast tables.

type ViewMode = "list" | "company" | "review";
type SortKey = "name" | "company" | "title" | "weight";

function ContactsList({ people }: { people: GraphNode[] }) {
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("weight");
  const [sortDir, setSortDir] = useState<1 | -1>(-1);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = !needle
      ? people
      : people.filter((p) =>
          [p.label, p.company, p.title, p.email].some((f) => (f || "").toLowerCase().includes(needle)),
        );
    out = [...out].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "name") cmp = (a.label || "").localeCompare(b.label || "");
      else if (sortKey === "company") cmp = (a.company || "").localeCompare(b.company || "");
      else if (sortKey === "title") cmp = (a.title || "").localeCompare(b.title || "");
      else cmp = (a.weight || 0) - (b.weight || 0);
      return cmp * sortDir;
    });
    return out;
  }, [people, q, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(key);
      setSortDir(key === "weight" ? -1 : 1);
    }
  };

  const arrow = (key: SortKey) => (key === sortKey ? (sortDir === 1 ? " ▲" : " ▼") : "");

  return (
    <div className="view-list">
      <div className="view-toolbar">
        <input
          className="view-search"
          placeholder="Search name, company, title, email…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="view-count">
          {rows.length} of {people.length}
        </div>
      </div>
      <div className="view-table-wrap">
        <table className="view-table">
          <thead>
            <tr>
              <th onClick={() => toggleSort("name")}>Name{arrow("name")}</th>
              <th onClick={() => toggleSort("title")}>Title{arrow("title")}</th>
              <th onClick={() => toggleSort("company")}>Company{arrow("company")}</th>
              <th>Email</th>
              <th onClick={() => toggleSort("weight")} style={{ textAlign: "right" }}>
                Contacted{arrow("weight")}
              </th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id}>
                <td className="vt-name">{p.label}</td>
                <td>{p.title || "—"}</td>
                <td>{p.company || "—"}</td>
                <td className="mono">{p.email || "—"}</td>
                <td style={{ textAlign: "right" }}>{p.weight ?? 0}×</td>
                <td>
                  <span className={`gc-tag ${p.enriched ? "on" : ""}`}>
                    {p.enriched ? "enriched" : "unenriched"}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="view-empty-row">
                  No matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// How many company cards to mount at once. A real mailbox groups into ~2,000 employers, and
// mounting every card (each carrying its whole member list) is ~20k DOM nodes in one
// synchronous render — the same thing that made the old force graph unusable. Cards stream in
// as the user scrolls instead.
const COMPANY_PAGE = 40;

function ContactsByCompany({ people }: { people: GraphNode[] }) {
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(COMPANY_PAGE);
  const sentinel = useRef<HTMLDivElement | null>(null);

  const groups = useMemo(() => {
    const by = new Map<string, GraphNode[]>();
    for (const p of people) {
      const key = p.company?.trim() || "Unknown company";
      if (!by.has(key)) by.set(key, []);
      by.get(key)!.push(p);
    }
    let out = Array.from(by.entries()).map(([name, members]) => ({ name, members }));
    out.sort((a, b) => b.members.length - a.members.length);
    const needle = q.trim().toLowerCase();
    if (needle) {
      out = out
        .map((g) => ({
          name: g.name,
          members: g.name.toLowerCase().includes(needle)
            ? g.members
            : g.members.filter((m) =>
                [m.label, m.title, m.email].some((f) => (f || "").toLowerCase().includes(needle)),
              ),
        }))
        .filter((g) => g.members.length > 0);
    }
    return out;
  }, [people, q]);

  // A new search is a new list — start from the top of it.
  useEffect(() => {
    setShown(COMPANY_PAGE);
  }, [q]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setShown((s) => (s >= groups.length ? s : s + COMPANY_PAGE));
        }
      },
      { rootMargin: "400px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [groups.length]);

  const visible = groups.slice(0, shown);

  return (
    <div className="view-list">
      <div className="view-toolbar">
        <input
          className="view-search"
          placeholder="Search company or person…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="view-count">{groups.length} companies</div>
      </div>
      <div className="view-companies">
        {visible.map((g) => (
          <details key={g.name} className="company-card" open={groups.length <= 8}>
            <summary>
              <span className="cc-name">{g.name}</span>
              <span className="cc-count">{g.members.length}</span>
            </summary>
            <ul className="cc-members">
              {g.members
                .slice()
                .sort((a, b) => (b.weight || 0) - (a.weight || 0))
                .map((m) => (
                  // Every cell is rendered even when empty. Skipping the title span
                  // collapsed the row's only flexible slot, so a contact with no title had
                  // its email slide left into the title column while everyone else's stayed
                  // right — the columns stopped lining up down the list.
                  <li key={m.id}>
                    <span className="vt-name">{m.label}</span>
                    <span className="cc-title">{m.title || ""}</span>
                    <span className="cc-email mono">{m.email || ""}</span>
                    <span className={`gc-tag ${m.enriched ? "on" : ""}`}>
                      {m.enriched ? "enriched" : "unenriched"}
                    </span>
                  </li>
                ))}
            </ul>
          </details>
        ))}
        {groups.length === 0 && <div className="view-empty-row">No matches.</div>}
        <div ref={sentinel} className="cl-sentinel" />
        {visible.length < groups.length && (
          <div className="cl-foot">
            Showing {visible.length.toLocaleString()} of {groups.length.toLocaleString()}{" "}
            companies — scroll for more
          </div>
        )}
      </div>
    </div>
  );
}

// How long to keep watching after an ingest/disconnect kicks off. The background task takes
// a couple of minutes; past this we stop asking rather than polling into the void.
const WATCH_MS = 5 * 60_000;

export default function ContactsGraph() {
  const contactsRefresh = useUiStore((s) => s.contactsRefresh);
  const [view, setView] = useState<ViewMode>("list");

  // Watch ONLY while an ingest is actually in flight. This used to poll every 5s for five
  // minutes on every single mount — a heavy FalkorDB read repeated ~60 times whether or not
  // anything was happening. Now the cached query serves revisits instantly, and the poll runs
  // only after `contactsRefresh` bumps (ingest / disconnect purge).
  const watchUntil = useRef(0);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false; // a plain visit is not a reason to watch
      return;
    }
    watchUntil.current = Date.now() + WATCH_MS;
  }, [contactsRefresh]);

  const q = useQuery({
    ...contactGraphQuery(),
    // Evaluated per tick, so watching stops on its own once the window closes.
    refetchInterval: () => (Date.now() < watchUntil.current ? 5000 : false),
  });
  const data = q.data ?? null;

  if (q.isError && !data)
    return <div className="graph-empty">Couldn&apos;t load the graph — is the backend + FalkorDB up?</div>;
  if (!data) return <div className="graph-empty">Loading network…</div>;
  if (data.nodes.length === 0)
    return (
      <div className="graph-empty">
        <div className="ge-t">No contacts yet</div>
        Connect Outlook and sync to build your contact list.
      </div>
    );

  const people = data.nodes.filter((n) => n.type === "Person");
  // Counted from the people themselves. The API used to ship a Company node per employer
  // purely so this line could count them — thousands of extra nodes for one number.
  const companies = new Set(people.map((p) => p.company).filter(Boolean)).size;

  const tabs: { key: ViewMode; label: string }[] = [
    { key: "list", label: "List" },
    { key: "company", label: "By company" },
    { key: "review", label: "To review" },
  ];

  const heading = (
    <div>
      <h2>Network</h2>
      <div className="graph-sub">
        {people.length} people · {companies} companies · from your Outlook
      </div>
    </div>
  );
  const tabBar = (
    <div className="view-tabs">
      {tabs.map((t) => (
        <button
          key={t.key}
          className={`view-tab ${view === t.key ? "on" : ""}`}
          onClick={() => setView(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );

  return (
    <div className="graph-wrap">
      <div className="graph-head">
        {heading}
        {tabBar}
      </div>
      {view === "list" && <ContactsList people={people} />}
      {view === "company" && <ContactsByCompany people={people} />}
      {view === "review" && <SuggestionsReview />}
    </div>
  );
}
