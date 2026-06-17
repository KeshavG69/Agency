"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchOpportunities,
  uploadExcel,
  runAnalyst,
  STAGES,
  type Opportunity,
  type BidDecision,
} from "@/lib/data";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const money = (n?: number | null) =>
  n == null
    ? "—"
    : n >= 1_000_000
      ? `$${(n / 1_000_000).toFixed(1)}M`
      : `$${(n / 1000).toFixed(0)}K`;

const badgeClass = (d?: BidDecision) =>
  d === "Bid" ? "bid" : d === "No-Bid" ? "nobid" : "watch";

function Card({ opp, onClick, index }: { opp: Opportunity; onClick: () => void; index: number }) {
  return (
    <button className="card" onClick={onClick} style={{ animationDelay: `${index * 45}ms` }}>
      <div className="ttl">{opp.title}</div>
      {opp.agency && <div className="agency">{opp.agency}</div>}
      <div className="meta">
        <span className="val">{money(opp.estimated_value)}</span>
        {opp.bid_decision && (
          <span className={`badge ${badgeClass(opp.bid_decision)}`}>{opp.bid_decision}</span>
        )}
      </div>
      <div className="meta" style={{ marginTop: 8 }}>
        {opp.set_aside && <span className="chip">{opp.set_aside}</span>}
        {opp.priority_score != null && (
          <span className="pscore">
            P <b>{opp.priority_score}</b>
          </span>
        )}
      </div>
    </button>
  );
}

function Drawer({ opp, onClose }: { opp: Opportunity; onClose: () => void }) {
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <button className="close" onClick={onClose}>
          ← close
        </button>
        <div style={{ marginTop: 14 }}>
          <h2>{opp.title}</h2>
          <div className="sub">
            {[opp.agency, opp.naics && `NAICS ${opp.naics}`, opp.set_aside]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>

        <div className="section">
          <div className="h">Analyst verdict</div>
          <div className="kv">
            <span className="k">Decision</span>
            {opp.bid_decision ? (
              <span className={`badge ${badgeClass(opp.bid_decision)}`}>{opp.bid_decision}</span>
            ) : (
              <span className="v">not analyzed</span>
            )}
          </div>
          <div className="kv">
            <span className="k">Priority</span>
            <span className="v">{opp.priority_score ?? "—"}</span>
          </div>
          <div className="kv">
            <span className="k">Value</span>
            <span className="v">{money(opp.estimated_value)}</span>
          </div>
          <div className="kv">
            <span className="k">Response due</span>
            <span className="v">{opp.response_deadline ?? "—"}</span>
          </div>
          {opp.analyst_rationale && (
            <p className="rationale" style={{ marginTop: 12 }}>
              “{opp.analyst_rationale}”
            </p>
          )}
        </div>

        {opp.calls && opp.calls.length > 0 && (
          <div className="section">
            <div className="h">Call plan</div>
            {opp.calls.map((c, i) => (
              <div className="li" key={i}>
                <div className="li-t">{c.name}</div>
                <div className="li-s">{c.talking_point}</div>
              </div>
            ))}
          </div>
        )}

        <div className="section">
          <div className="h">Documents</div>
          {opp.documents && opp.documents.length > 0 ? (
            opp.documents.map((d, i) => (
              <div className="li" key={i}>
                <div className="li-t">{d.title}</div>
                <div className="li-s" style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>
                    {d.type} · {d.status}
                  </span>
                  <a href={d.url}>open ↗</a>
                </div>
              </div>
            ))
          ) : (
            <div className="li-s" style={{ color: "var(--faint)" }}>
              No documents yet — generated during capture.
            </div>
          )}
        </div>

        <div className="drawer-actions">
          <button className="btn">Approve for capture</button>
          <button className="btn ghost">Open in CRM</button>
        </div>
      </aside>
    </>
  );
}

export default function Dashboard() {
  const [opps, setOpps] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Opportunity | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOpps(await fetchOpportunities());
    } catch {
      setError("Can't reach the backend (start it on :8000).");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await uploadExcel(file);
      await load();
    } catch {
      setError("Upload failed — check the backend + API keys.");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onRunAnalyst = async () => {
    setAnalyzing(true);
    setError(null);
    try {
      await runAnalyst(); // queues the Celery task; verdicts arrive as the worker finishes
      for (let i = 0; i < 12; i++) {
        await sleep(3000);
        const fresh = await fetchOpportunities();
        setOpps(fresh);
        if (fresh.length > 0 && fresh.every((o) => o.bid_decision)) break;
      }
    } catch {
      setError("Run analyst failed — is the backend + Celery worker running?");
    } finally {
      setAnalyzing(false);
    }
  };

  const total = opps.length;
  const toPursue = opps.filter((o) => o.bid_decision === "Bid").length;

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <div className="wordmark">
            Collecct<span className="dot">.</span>
          </div>
          <div className="tagline">Capture operations · Nexagen Networks</div>
        </div>
        <div className="topbar-right">
          <div className="stat">
            <div className="num">{total}</div>
            <div className="lbl">Opportunities</div>
          </div>
          <div className="stat">
            <div className="num">{toPursue}</div>
            <div className="lbl">To pursue</div>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls"
            onChange={onUpload}
            style={{ display: "none" }}
          />
          <button
            className="btn ghost"
            onClick={onRunAnalyst}
            disabled={analyzing || total === 0}
          >
            {analyzing ? "Analyzing…" : "Run Analyst"}
          </button>
          <button className="btn" onClick={() => fileRef.current?.click()}>
            Upload Excel
          </button>
        </div>
      </header>

      {loading && <p className="empty">Loading…</p>}
      {error && <p className="empty">{error}</p>}
      {!loading && !error && total === 0 && (
        <p className="empty">No opportunities yet — upload an Excel to begin.</p>
      )}

      {!loading && !error && total > 0 && (
        <section className="board">
          {STAGES.map((stage) => {
            const items = opps.filter((o) => o.stage === stage);
            return (
              <div className="col" key={stage}>
                <div className="col-head">
                  <span className="name">{stage}</span>
                  <span className="count">{items.length}</span>
                </div>
                <div className="col-body">
                  {items.map((opp, i) => (
                    <Card key={opp.id} opp={opp} index={i} onClick={() => setSelected(opp)} />
                  ))}
                </div>
              </div>
            );
          })}
        </section>
      )}

      {selected && <Drawer opp={selected} onClose={() => setSelected(null)} />}
    </main>
  );
}
