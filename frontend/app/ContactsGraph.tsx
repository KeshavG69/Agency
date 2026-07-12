"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchContactGraph, type ContactGraph, type GraphNode } from "@/lib/data";
import ForceGraph, { type NodeVisual } from "./ForceGraph";

const visual = (n: GraphNode): NodeVisual => {
  if (n.type === "Company") {
    return { color: "var(--ink)", r: 9, square: true, label: n.label };
  }
  const r = 5 + Math.min(Math.sqrt(n.weight || 1) * 1.6, 9);
  return {
    color: n.enriched ? "var(--accent)" : "var(--faint)",
    r,
    square: false,
    label: (n.label || "").split(" ")[0].slice(0, 16),
  };
};

const card = (n: GraphNode) =>
  n.type === "Person" ? (
    <>
      <div className="gc-name">{n.label}</div>
      {n.title && <div className="gc-row">{n.title}</div>}
      {n.company && <div className="gc-row">{n.company}</div>}
      {n.email && <div className="gc-row mono">{n.email}</div>}
      <div className="gc-meta">
        <span>{n.weight}× contacted</span>
        <span className={`gc-tag ${n.enriched ? "on" : ""}`}>
          {n.enriched ? "enriched" : "unenriched"}
        </span>
      </div>
    </>
  ) : (
    <>
      <div className="gc-name">{n.label}</div>
      <div className="gc-row">Company</div>
    </>
  );

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type ViewMode = "graph" | "list" | "company";
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

function ContactsByCompany({ people }: { people: GraphNode[] }) {
  const [q, setQ] = useState("");

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
        {groups.map((g) => (
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
                  <li key={m.id}>
                    <span className="vt-name">{m.label}</span>
                    {m.title && <span className="cc-title">{m.title}</span>}
                    {m.email && <span className="cc-email mono">{m.email}</span>}
                    <span className={`gc-tag ${m.enriched ? "on" : ""}`}>
                      {m.enriched ? "enriched" : "unenriched"}
                    </span>
                  </li>
                ))}
            </ul>
          </details>
        ))}
        {groups.length === 0 && <div className="view-empty-row">No matches.</div>}
      </div>
    </div>
  );
}

export default function ContactsGraph({ refreshSignal }: { refreshSignal?: number } = {}) {
  const [data, setData] = useState<ContactGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("graph");

  // Contact ingestion (and disconnect's purge) runs/completes in the background — rather
  // than making the user manually reload the page to see it land, poll for a while and
  // pick it up automatically. Re-fetches on mount and every time `refreshSignal` changes
  // (bumped by the parent on ingest / disconnect). Mirrors SharePointGraph's polling.
  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    const maxAttempts = 60; // ~5 min at 5s intervals

    const tick = async () => {
      try {
        const fresh = await fetchContactGraph();
        if (cancelled) return;
        setErr(null);
        setData((prev) =>
          prev && prev.nodes.length === fresh.nodes.length && prev.edges.length === fresh.edges.length
            ? prev
            : fresh,
        );
      } catch {
        if (!cancelled) setErr("Couldn't load the graph — is the backend + FalkorDB up?");
      }
      attempt++;
      if (!cancelled && attempt < maxAttempts) {
        await sleep(5000);
        if (!cancelled) tick();
      }
    };

    tick();
    return () => {
      cancelled = true;
    };
  }, [refreshSignal]);

  if (err) return <div className="graph-empty">{err}</div>;
  if (!data) return <div className="graph-empty">Loading network…</div>;
  if (data.nodes.length === 0)
    return (
      <div className="graph-empty">
        <div className="ge-t">No contacts yet</div>
        Connect Outlook and sync to build the network graph.
      </div>
    );

  const people = data.nodes.filter((n) => n.type === "Person");
  const companies = data.nodes.filter((n) => n.type === "Company").length;

  const tabs: { key: ViewMode; label: string }[] = [
    { key: "graph", label: "Graph" },
    { key: "list", label: "List" },
    { key: "company", label: "By company" },
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

  if (view === "graph") {
    return (
      <ForceGraph
        nodes={data.nodes}
        edges={data.edges}
        visual={visual}
        card={card}
        header={heading}
        legend={
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div className="graph-legend">
              <span>
                <i className="dot" style={{ background: "var(--accent)" }} /> enriched
              </span>
              <span>
                <i className="dot" style={{ background: "var(--faint)" }} /> unenriched
              </span>
              <span>
                <i className="dot sq" style={{ background: "var(--ink)" }} /> company
              </span>
            </div>
            {tabBar}
          </div>
        }
      />
    );
  }

  return (
    <div className="graph-wrap">
      <div className="graph-head">
        {heading}
        {tabBar}
      </div>
      {view === "list" && <ContactsList people={people} />}
      {view === "company" && <ContactsByCompany people={people} />}
    </div>
  );
}
