"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type SPNode } from "@/lib/data";
import { sharePointGraphQuery } from "@/lib/queries";
import { useUiStore } from "@/lib/stores/uiStore";
import ForceGraph, { type NodeVisual } from "./ForceGraph";

const TYPE: Record<string, { color: string; r: number; square?: boolean }> = {
  site: { color: "var(--ink)", r: 9, square: true },
  library: { color: "var(--accent)", r: 7 },
  list: { color: "var(--watch)", r: 7 },
  folder: { color: "var(--faint)", r: 5 },
  file: { color: "var(--line-strong)", r: 4 },
};

const visual = (n: SPNode): NodeVisual => {
  const t = TYPE[n.type] ?? TYPE.folder;
  const showLabel = n.type === "site" || n.type === "library" || n.type === "list";
  return { color: t.color, r: t.r, square: t.square, label: showLabel ? n.name.slice(0, 22) : null };
};

const card = (n: SPNode) => (
  <>
    <div className="gc-name">{n.name}</div>
    <div
      className="gc-row"
      style={{ textTransform: "uppercase", fontSize: 10, letterSpacing: "0.08em", color: "var(--faint)" }}
    >
      {n.type}
      {n.item_count != null ? ` · ${n.item_count} items` : ""}
    </div>
    {n.path && <div className="gc-row mono">{n.path}</div>}
    {n.web_url && (
      <div style={{ marginTop: 8 }}>
        <a href={n.web_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)", fontSize: 12.5 }}>
          Open in SharePoint ↗
        </a>
      </div>
    )}
  </>
);


type ViewMode = "graph" | "list" | "site";
type SortKey = "name" | "type" | "path" | "items";

function SPList({ nodes }: { nodes: SPNode[] }) {
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("path");
  const [sortDir, setSortDir] = useState<1 | -1>(1);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = !needle
      ? nodes
      : nodes.filter((n) => [n.name, n.path, n.type].some((f) => (f || "").toLowerCase().includes(needle)));
    out = [...out].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "name") cmp = (a.name || "").localeCompare(b.name || "");
      else if (sortKey === "type") cmp = (a.type || "").localeCompare(b.type || "");
      else if (sortKey === "path") cmp = (a.path || "").localeCompare(b.path || "");
      else cmp = (a.item_count || 0) - (b.item_count || 0);
      return cmp * sortDir;
    });
    return out;
  }, [nodes, q, sortKey, sortDir]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir((d) => (d === 1 ? -1 : 1));
    else {
      setSortKey(key);
      setSortDir(1);
    }
  };
  const arrow = (key: SortKey) => (key === sortKey ? (sortDir === 1 ? " ▲" : " ▼") : "");

  return (
    <div className="view-list">
      <div className="view-toolbar">
        <input
          className="view-search"
          placeholder="Search name, path, type…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="view-count">
          {rows.length} of {nodes.length}
        </div>
      </div>
      <div className="view-table-wrap">
        <table className="view-table">
          <thead>
            <tr>
              <th onClick={() => toggleSort("name")}>Name{arrow("name")}</th>
              <th onClick={() => toggleSort("type")}>Type{arrow("type")}</th>
              <th onClick={() => toggleSort("path")}>Path{arrow("path")}</th>
              <th onClick={() => toggleSort("items")} style={{ textAlign: "right" }}>
                Items{arrow("items")}
              </th>
              <th>Link</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((n) => (
              <tr key={n.id}>
                <td className="vt-name">{n.name}</td>
                <td style={{ textTransform: "capitalize" }}>{n.type}</td>
                <td className="mono">{n.path || "—"}</td>
                <td style={{ textAlign: "right" }}>{n.item_count ?? "—"}</td>
                <td>
                  {n.web_url ? (
                    <a href={n.web_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                      Open ↗
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="view-empty-row">
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

function SPBySite({ nodes, edges }: { nodes: SPNode[]; edges: { source: string; target: string }[] }) {
  const [q, setQ] = useState("");

  const groups = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const parentOf = new Map<string, string>();
    for (const e of edges) parentOf.set(e.target, e.source);

    const siteCache = new Map<string, SPNode | null>();
    const siteOf = (id: string, seen: Set<string> = new Set()): SPNode | null => {
      if (siteCache.has(id)) return siteCache.get(id)!;
      if (seen.has(id)) return null;
      seen.add(id);
      const n = byId.get(id);
      if (!n) return null;
      if (n.type === "site") {
        siteCache.set(id, n);
        return n;
      }
      const pid = parentOf.get(id);
      const result = pid ? siteOf(pid, seen) ?? n : n;
      siteCache.set(id, result);
      return result;
    };

    const by = new Map<string, { site: SPNode; members: SPNode[] }>();
    for (const n of nodes) {
      if (n.type === "site") continue;
      const site = siteOf(n.id) ?? n;
      if (!by.has(site.id)) by.set(site.id, { site, members: [] });
      by.get(site.id)!.members.push(n);
    }
    let out = Array.from(by.values());
    out.sort((a, b) => b.members.length - a.members.length);

    const needle = q.trim().toLowerCase();
    if (needle) {
      out = out
        .map((g) => ({
          site: g.site,
          members: g.site.name.toLowerCase().includes(needle)
            ? g.members
            : g.members.filter((m) => [m.name, m.path].some((f) => (f || "").toLowerCase().includes(needle))),
        }))
        .filter((g) => g.members.length > 0);
    }
    return out;
  }, [nodes, edges, q]);

  return (
    <div className="view-list">
      <div className="view-toolbar">
        <input
          className="view-search"
          placeholder="Search site, library, or folder…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className="view-count">{groups.length} sites</div>
      </div>
      <div className="view-companies">
        {groups.map((g) => (
          <details key={g.site.id} className="company-card" open={groups.length <= 8}>
            <summary>
              <span className="cc-name">{g.site.name}</span>
              <span className="cc-count">{g.members.length}</span>
            </summary>
            <ul className="cc-members">
              {g.members
                .slice()
                .sort((a, b) => (a.path || "").localeCompare(b.path || ""))
                .map((m) => (
                  <li key={m.id}>
                    <span className="vt-name">{m.name}</span>
                    <span className="cc-title mono">{m.path || ""}</span>
                    <span className="gc-tag on" style={{ textTransform: "capitalize" }}>
                      {m.type}
                    </span>
                    {m.web_url && (
                      <a href={m.web_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                        Open ↗
                      </a>
                    )}
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

// A real structure crawl (one Composio call to Microsoft per item) takes a few minutes;
// past this we stop watching rather than polling into the void.
const WATCH_MS = 5 * 60_000;

export default function SharePointGraph() {
  const sharePointRefresh = useUiStore((s) => s.sharePointRefresh);
  const [view, setView] = useState<ViewMode>("graph");

  // Watch ONLY while a crawl is actually in flight (a resync click, or landing back here
  // right after connecting — both bump `sharePointRefresh`). This used to poll every 5s for
  // five minutes on every mount regardless; the cached query now serves revisits instantly.
  const watchUntil = useRef(0);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false; // a plain visit is not a reason to watch
      return;
    }
    watchUntil.current = Date.now() + WATCH_MS;
  }, [sharePointRefresh]);

  const q = useQuery({
    ...sharePointGraphQuery(),
    // Evaluated per tick, so watching stops on its own once the window closes.
    refetchInterval: () => (Date.now() < watchUntil.current ? 5000 : false),
  });
  const data = q.data ?? null;

  if (q.isError && !data)
    return (
      <div className="graph-empty">
        Couldn&apos;t load the SharePoint graph — connect SharePoint and sync first.
      </div>
    );
  if (!data) return <div className="graph-empty">Loading structure…</div>;
  if (data.nodes.length === 0)
    return (
      <div className="graph-empty">
        <div className="ge-t">No SharePoint structure yet</div>
        Connect SharePoint and run the structure sync.
      </div>
    );

  const s = data.stats;
  const tabs: { key: ViewMode; label: string }[] = [
    { key: "graph", label: "Graph" },
    { key: "list", label: "List" },
    { key: "site", label: "By site" },
  ];

  const heading = (
    <div>
      <h2>Documents</h2>
      <div className="graph-sub">
        {s
          ? `${s.sites} sites · ${s.libraries} libraries · ${s.folders} folders · ${s.files} files`
          : "SharePoint structure"}{" "}
        · files hidden for clarity
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
        charge={1700}
        linkDist={60}
        header={heading}
        legend={
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div className="graph-legend">
              <span>
                <i className="dot sq" style={{ background: "var(--ink)" }} /> site
              </span>
              <span>
                <i className="dot" style={{ background: "var(--accent)" }} /> library
              </span>
              <span>
                <i className="dot" style={{ background: "var(--watch)" }} /> list
              </span>
              <span>
                <i className="dot" style={{ background: "var(--faint)" }} /> folder
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
      {view === "list" && <SPList nodes={data.nodes.filter((n) => n.type !== "site")} />}
      {view === "site" && <SPBySite nodes={data.nodes} edges={data.edges} />}
    </div>
  );
}
