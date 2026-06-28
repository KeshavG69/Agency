"use client";

import { useEffect, useState } from "react";
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

export default function ContactsGraph() {
  const [data, setData] = useState<ContactGraph | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchContactGraph()
      .then(setData)
      .catch(() => setErr("Couldn't load the graph — is the backend + FalkorDB up?"));
  }, []);

  if (err) return <div className="graph-empty">{err}</div>;
  if (!data) return <div className="graph-empty">Loading network…</div>;
  if (data.nodes.length === 0)
    return (
      <div className="graph-empty">
        <div className="ge-t">No contacts yet</div>
        Connect Outlook and sync to build the network graph.
      </div>
    );

  const people = data.nodes.filter((n) => n.type === "Person").length;
  const companies = data.nodes.filter((n) => n.type === "Company").length;

  return (
    <ForceGraph
      nodes={data.nodes}
      edges={data.edges}
      visual={visual}
      card={card}
      header={
        <div>
          <h2>Network</h2>
          <div className="graph-sub">
            {people} people · {companies} companies · from your Outlook
          </div>
        </div>
      }
      legend={
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
      }
    />
  );
}
