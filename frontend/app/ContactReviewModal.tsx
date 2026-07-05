"use client";

import { useMemo, useState } from "react";
import type { ContactCandidate } from "@/lib/data";

/**
 * Review dialog shown before any Outlook contact touches the graph. Candidates are
 * split into Work / Personal; Work is pre-ticked (that's the BD-relevant set). The
 * user toggles whichever they want, then confirms — only the ticked contacts get
 * enriched + graphed. Mirrors the "choose what to import" pattern users expect.
 */
export default function ContactReviewModal({
  contacts,
  loading,
  error,
  onConfirm,
  onClose,
}: {
  contacts: ContactCandidate[];
  loading: boolean;
  error: string | null;
  onConfirm: (selected: ContactCandidate[]) => void;
  onClose: () => void;
}) {
  // Selection keyed by email. Work is auto-selected; personal starts off.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(contacts.filter((c) => c.category === "work").map((c) => c.email)),
  );

  const { work, personal } = useMemo(() => {
    const w: ContactCandidate[] = [];
    const p: ContactCandidate[] = [];
    for (const c of contacts) (c.category === "work" ? w : p).push(c);
    return { work: w, personal: p };
  }, [contacts]);

  const toggle = (email: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(email) ? next.delete(email) : next.add(email);
      return next;
    });

  const toggleGroup = (group: ContactCandidate[], on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      group.forEach((c) => (on ? next.add(c.email) : next.delete(c.email)));
      return next;
    });

  const confirm = () => onConfirm(contacts.filter((c) => selected.has(c.email)));

  const Group = ({ title, list, tone }: { title: string; list: ContactCandidate[]; tone: string }) => {
    if (list.length === 0) return null;
    const allOn = list.every((c) => selected.has(c.email));
    const n = list.filter((c) => selected.has(c.email)).length;
    return (
      <div className="crm-group">
        <div className="crm-group-head">
          <span className={`crm-tag ${tone}`}>{title}</span>
          <span className="crm-group-count">
            {n}/{list.length} selected
          </span>
          <button className="sel-link" onClick={() => toggleGroup(list, !allOn)}>
            {allOn ? "Deselect all" : "Select all"}
          </button>
        </div>
        <div className="crm-rows">
          {list.map((c) => (
            <label className={`crm-row ${selected.has(c.email) ? "on" : ""}`} key={c.email}>
              <input
                type="checkbox"
                checked={selected.has(c.email)}
                onChange={() => toggle(c.email)}
              />
              <span className="crm-check" aria-hidden />
              <span className="crm-main">
                <span className="crm-name">{c.name || c.email.split("@")[0]}</span>
                <span className="crm-email">{c.email}</span>
              </span>
              <span className="crm-meta">
                {c.company && <span className="crm-co">{c.company}</span>}
                {c.count ? <span className="crm-count">{c.count} emails</span> : null}
              </span>
            </label>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="crm-backdrop" onClick={onClose}>
      <div className="crm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="crm-head">
          <div>
            <h2>Choose contacts to import</h2>
            <div className="crm-sub">
              Work contacts are pre-selected. Untick anything you don&apos;t want — only the
              selected contacts are added to your network graph.
            </div>
          </div>
          <button className="crm-x" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="crm-body">
          {loading ? (
            <div className="crm-state">
              <span className="spin" /> Reading your Outlook contacts…
            </div>
          ) : error ? (
            <div className="crm-state crm-err">{error}</div>
          ) : contacts.length === 0 ? (
            <div className="crm-state">No external contacts found in your mailbox.</div>
          ) : (
            <>
              <Group title="Work" list={work} tone="work" />
              <Group title="Personal" list={personal} tone="personal" />
            </>
          )}
        </div>

        <div className="crm-foot">
          <span className="crm-total">
            {selected.size} of {contacts.length} selected
          </span>
          <div className="crm-actions">
            <button className="sel-link" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn primary"
              onClick={confirm}
              disabled={loading || !!error}
            >
              Import {selected.size} contact{selected.size === 1 ? "" : "s"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
