"use client";

import { useEffect, useState } from "react";
import type { TeamMember } from "@/lib/types";

/**
 * Assign an opportunity to org members — modeled on PriceIQ's ShareProposalModal:
 * a dialog listing team members with checkboxes; Save applies the selection.
 * No members selected = unassigned (visible to everyone).
 */
export default function AssignModal({
  oppTitle,
  members,
  assigned,
  onSave,
  onClose,
}: {
  oppTitle: string;
  members: TeamMember[];
  assigned: string[];
  onSave: (userIds: string[]) => Promise<void> | void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(assigned));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const save = async () => {
    setSaving(true);
    try {
      await onSave([...selected]);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="preview-overlay" onClick={onClose}>
      <div className="assign-modal" onClick={(e) => e.stopPropagation()}>
        <div className="am-head">
          <div style={{ minWidth: 0 }}>
            <div className="am-title">Assign opportunity</div>
            <div className="am-sub">{oppTitle}</div>
          </div>
          <button className="preview-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="am-body">
          <div className="am-hint">
            Select members to assign. Unassigned opportunities stay visible to everyone.
          </div>
          {members.length === 0 ? (
            <div className="am-empty">No members in this organization yet.</div>
          ) : (
            members.map((m) => {
              const on = selected.has(m.id);
              return (
                <button
                  key={m.id}
                  type="button"
                  className={`am-row ${on ? "on" : ""}`}
                  onClick={() => toggle(m.id)}
                  aria-pressed={on}
                >
                  <span className={`am-check ${on ? "on" : ""}`}>{on ? "✓" : ""}</span>
                  <span className="am-name">
                    {`${m.firstName ?? ""} ${m.lastName ?? ""}`.trim() || m.email}
                    <span className="am-email">{m.email}</span>
                  </span>
                  {m.role === "admin" && <span className="am-role">admin</span>}
                </button>
              );
            })
          )}
        </div>

        <div className="am-foot">
          <button className="preview-btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="mini-btn" onClick={save} disabled={saving}>
            {saving ? "Saving…" : selected.size > 0 ? `Assign ${selected.size}` : "Unassign"}
          </button>
        </div>
      </div>
    </div>
  );
}
