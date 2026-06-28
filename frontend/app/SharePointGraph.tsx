"use client";

import { useEffect, useState } from "react";
import { fetchSharePointGraph, type SPGraph, type SPNode } from "@/lib/data";
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

export default function SharePointGraph() {
  const [data, setData] = useState<SPGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchSharePointGraph()
      .then(setData)
      .catch(() => setErr("Couldn't load the SharePoint graph — connect SharePoint and sync first."));
  }, []);

  if (err) return <div className="graph-empty">{err}</div>;
  if (!data) return <div className="graph-empty">Loading structure…</div>;
  if (data.nodes.length === 0)
    return (
      <div className="graph-empty">
        <div className="ge-t">No SharePoint structure yet</div>
        Connect SharePoint and run the structure sync.
      </div>
    );

  const s = data.stats;
  return (
    <ForceGraph
      nodes={data.nodes}
      edges={data.edges}
      visual={visual}
      card={card}
      charge={1700}
      linkDist={60}
      header={
        <div>
          <h2>Documents</h2>
          <div className="graph-sub">
            {s
              ? `${s.sites} sites · ${s.libraries} libraries · ${s.folders} folders · ${s.files} files`
              : "SharePoint structure"}{" "}
            · files hidden for clarity
          </div>
        </div>
      }
      legend={
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
      }
    />
  );
}
