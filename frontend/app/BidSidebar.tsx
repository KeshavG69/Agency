"use client";

import { useMemo } from "react";
import type { Opportunity } from "@/lib/data";
import { dueLabel } from "@/lib/format";

/**
 * The repurposed left sidebar: a persistent list of your Bid-phase opportunities
 * ("active pursuits"), soonest deadline first. Always visible; clicking one opens
 * it in the Pipeline detail. This replaces the old nav rail.
 */
export default function BidSidebar({
  bids,
  selectedId,
  onOpen,
}: {
  bids: Opportunity[];
  selectedId: string | null;
  onOpen: (id: string) => void;
}) {
  // Soonest deadline first; opportunities without a deadline sink to the bottom.
  const ordered = useMemo(
    () =>
      [...bids].sort((a, b) => {
        const da = dueLabel(a.response_deadline).days;
        const db = dueLabel(b.response_deadline).days;
        return (da ?? Infinity) - (db ?? Infinity);
      }),
    [bids],
  );

  return (
    <aside className="bidbar">
      <div className="bidbar-head">
        <span className="bb-title">Active pursuits</span>
        <span className="bb-count">{bids.length}</span>
      </div>
      <div className="bidbar-list">
        {ordered.length === 0 ? (
          <div className="bidbar-empty">
            No active pursuits yet. Mark an opportunity <b>Bid</b> to pin it here.
          </div>
        ) : (
          ordered.map((o) => {
            const due = dueLabel(o.response_deadline);
            return (
              <button
                key={o.id}
                className={`bb-row ${o.id === selectedId ? "sel" : ""}`}
                onClick={() => onOpen(o.id)}
              >
                <div className="bb-main">
                  <div className="bb-name">{o.title}</div>
                  <div className="bb-sub">{o.agency ?? "—"}</div>
                </div>
                <span className={`due-chip ${due.tone}`}>{due.text}</span>
              </button>
            );
          })
        )}
      </div>
    </aside>
  );
}
