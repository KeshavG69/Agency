"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { buildCapability, type CapRequirement, type Opportunity } from "@/lib/data";
import { capabilityQuery, queryKeys } from "@/lib/queries";
import { useToastStore } from "@/lib/stores/toastStore";

/**
 * How strong we are at each thing the government wants done, from 1 (nothing in the library) to
 * 5 (a recent contract doing exactly this). Every score is capped by proof the server checked
 * against the library file, so a high score always has a contract behind it.
 */
export default function CapabilityTab({ opp }: { opp: Opportunity }) {
  const toast = useToastStore((s) => s.push);
  const qc = useQueryClient();
  const q = useQuery(capabilityQuery(opp.id));
  const [gapsOnly, setGapsOnly] = useState(false);
  const [legend, setLegend] = useState(false);
  const build = useMutation({
    mutationFn: () => buildCapability(opp.id),
    onSuccess: (m) => qc.setQueryData(queryKeys.capability(opp.id), m),
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast(detail ?? "Couldn't start the matrix — try again.");
    },
  });

  if (q.isLoading) return <p className="req-muted">Loading…</p>;
  const m = q.data;
  const building = m?.status === "building" || build.isPending;
  const areas = m?.areas ?? [];

  if (!areas.length) {
    return (
      <div className="req-empty">
        {building ? (
          <p className="req-muted">Building the capability matrix. This takes a few minutes…</p>
        ) : (
          <>
            <p className="req-muted">
              {m?.status === "failed" && m.error
                ? m.error
                : "Rate the company against each thing this solicitation asks for, from your library, and list the gaps."}
            </p>
            <button className="mini-btn" onClick={() => build.mutate()}>
              {m?.status === "failed" ? "Try again" : "Build the matrix"}
            </button>
          </>
        )}
      </div>
    );
  }

  const s = m?.stats;
  return (
    <div className="cap">
      <div className="req-head">
        <div className="req-progress">
          {s && (
            <span className="req-count">
              {s.requirements} requirements · {s.strong} strong · {s.gaps} gaps
              {s.average != null && <> · average {s.average}/5</>}
            </span>
          )}
          {building && <span className="req-muted">Rebuilding… the matrix below is the last one.</span>}
          {m?.status === "failed" && m.error && <span className="req-flag">{m.error}</span>}
        </div>
        <div className="req-head-actions">
          {m?.file?.edit_url && (
            <a className="sel-link" href={m.file.edit_url} target="_blank" rel="noopener noreferrer">
              Open in Excel
            </a>
          )}
          <button className="sel-link" onClick={() => setGapsOnly((g) => !g)}>
            {gapsOnly ? "Show all" : "Show gaps only"}
          </button>
          <button className="mini-btn" disabled={building} onClick={() => build.mutate()}>
            {building ? "Building…" : "Rebuild"}
          </button>
        </div>
      </div>
      {s?.library_files === 0 && (
        <p className="req-warn">
          Your library has no company documents yet, so every requirement shows as a gap. Add past
          performance, resumes and past proposals on the Library page, then rebuild.
        </p>
      )}
      {m?.file_error && <p className="req-muted">Excel copy not saved: {m.file_error}</p>}

      <button className="sel-link cap-legend-toggle" onClick={() => setLegend((l) => !l)}>
        {legend ? "Hide how scores work" : "How scores work"}
      </button>
      {legend && (
        <ul className="cap-legend">
          {Object.entries(m?.rubric ?? {})
            .sort(([a], [b]) => Number(b) - Number(a))
            .map(([k, v]) => (
              <li key={k}>
                <Score n={Number(k)} /> {v}
              </li>
            ))}
        </ul>
      )}

      {gapsOnly && s?.gaps === 0 && <p className="req-muted cap-area">No gaps: every requirement scored 3 or higher.</p>}
      {areas.map((a) => {
        const reqs = gapsOnly ? a.requirements.filter((r) => r.score <= 2) : a.requirements;
        if (!reqs.length) return null;
        const avg = a.requirements.reduce((t, r) => t + r.score, 0) / a.requirements.length;
        return (
          <section key={a.name} className="cap-area">
            <h3 className="cap-area-name">
              {a.name} <span className="req-kind">average {avg.toFixed(1)}/5</span>
            </h3>
            <ul className="req-list">
              {reqs.map((r) => (
                <Row key={r.id} r={r} />
              ))}
            </ul>
          </section>
        );
      })}

      {(m?.contacts?.length ?? 0) > 0 && (
        <section className="cap-area">
          <h3 className="cap-area-name">Government contacts</h3>
          <ul className="cap-contacts">
            {m!.contacts!.map((c, i) => (
              <li key={i}>
                <strong>{c.name}</strong>
                {c.role && <> · {c.role}</>}
                {c.email && <> · <a href={`mailto:${c.email}`}>{c.email}</a></>}
                {c.phone && <> · {c.phone}</>}
                {!c.verified && <span className="req-flag req-flag-inline">Check this</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Score({ n }: { n: number }) {
  const tone = n >= 4 ? "strong" : n === 3 ? "mid" : "weak";
  return <span className={`cap-score ${tone}`} aria-label={`Score ${n} of 5`}>{n}</span>;
}

function Row({ r }: { r: CapRequirement }) {
  const [open, setOpen] = useState(false);
  const proof = r.evidence.filter((e) => e.verified);
  return (
    <li className="req-item">
      <Score n={r.score} />
      <div className="req-main">
        <div className="req-title-row">
          <button className="req-title" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {r.text}
          </button>
          {r.source_ref && <span className="req-limit">{r.source_ref}</span>}
          {!r.verified && (
            <span className="req-flag" title="We couldn't find these exact words in the solicitation">
              Check this
            </span>
          )}
        </div>
        {r.score <= 2 && r.gap && (
          <p className="cap-gap">
            Gap: {r.gap}
            {r.action && <> — {r.action}</>}
          </p>
        )}
        {proof.length > 0 && !open && (
          <p className="req-detail">Proof: {proof.map((e) => e.name || e.file).join(", ")}</p>
        )}
        {open && (
          <div className="req-more">
            <p className="req-detail">{r.rationale}</p>
            {proof.map((e, i) => (
              <p key={i} className="req-detail">
                <strong>{e.name || e.file}</strong>: {e.claim}{" "}
                {e.web_url && (
                  <a className="sel-link" href={e.web_url} target="_blank" rel="noopener noreferrer">
                    open file
                  </a>
                )}
              </p>
            ))}
            {r.quote && <p className="req-quote">“{r.quote}”</p>}
          </div>
        )}
      </div>
    </li>
  );
}
