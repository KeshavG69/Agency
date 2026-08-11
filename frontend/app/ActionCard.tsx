"use client";

import { useState } from "react";

import type { ActionItem, ActionKind } from "@/lib/data";

/**
 * One thing to do, today.
 *
 * THE RULE THIS CARD EXISTS TO ENFORCE: the primary button DOES the thing. The complaint
 * that started this feature was that the Pipeline shows records and leaves the rep to work
 * out what to do about each one — so a card that merely linked to the record would rebuild
 * exactly that problem in a new view. Every kind below maps to an action that either fires
 * an endpoint here or opens the tool that finishes the job.
 *
 * Done / Snooze / Dismiss are always available and always secondary. `infeasible` is the one
 * inversion: when there is genuinely not enough runway left, Dismiss leads, because the
 * honest next step is to drop it.
 */

const KIND_LABEL: Record<ActionKind, string> = {
  analyze: "Analyse",
  decide: "Decide",
  approve_capture: "Capture",
  retry_capture: "Capture",
  call: "Call",
  review_docs: "Review",
  submit: "Submit",
  reply_mail: "Reply",
};

const SNOOZE_OPTIONS: { label: string; days: number }[] = [
  { label: "Tomorrow", days: 1 },
  { label: "In 3 days", days: 3 },
  { label: "Next week", days: 7 },
];

export interface ActionHandlers {
  onDone: (a: ActionItem) => void;
  onDismiss: (a: ActionItem) => void;
  onSnooze: (a: ActionItem, days: number) => void;
  /** The kind-specific primary control. Each opens or fires the thing that finishes the job. */
  onAnalyze: (a: ActionItem) => void;
  onDecide: (a: ActionItem, decision: "Bid" | "No-Bid") => void;
  onApproveCapture: (a: ActionItem) => void;
  onPrepCall: (a: ActionItem) => void;
  onOpenDocuments: (a: ActionItem) => void;
  onOpenOpportunity: (a: ActionItem) => void;
  onReplyMail: (a: ActionItem) => void;
}

export default function ActionCard({
  action,
  handlers,
  busy,
}: {
  action: ActionItem;
  handlers: ActionHandlers;
  busy?: boolean;
}) {
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const closes = closesLabel(action.closes_in_days);

  return (
    <li className={`act ${action.urgency} ${action.infeasible ? "infeasible" : ""}`}>
      <div className="act-main">
        <div className="act-top">
          <span className={`act-kind k-${action.kind}`}>{KIND_LABEL[action.kind]}</span>
          <h3 className="act-title">{action.title}</h3>
        </div>
        <p className="act-reason">{action.reason}</p>
      </div>

      <div className="act-side">
        {closes && (
          <span className={`act-due t-${closes.tone}`} title={`Closes ${action.hard_deadline}`}>
            {closes.text}
          </span>
        )}
        <div className="act-do">{primary(action, handlers, busy)}</div>
        <div className="act-secondary">
          {!action.infeasible && (
            <button className="act-link" onClick={() => handlers.onDone(action)} disabled={busy}>
              Done
            </button>
          )}
          <span className="act-snooze">
            <button
              className="act-link"
              onClick={() => setSnoozeOpen((v) => !v)}
              disabled={busy}
              aria-expanded={snoozeOpen}
            >
              Snooze ▾
            </button>
            {snoozeOpen && (
              <span className="act-snooze-menu">
                {SNOOZE_OPTIONS.map((o) => (
                  <button
                    key={o.days}
                    onClick={() => {
                      setSnoozeOpen(false);
                      handlers.onSnooze(action, o.days);
                    }}
                  >
                    {o.label}
                  </button>
                ))}
              </span>
            )}
          </span>
          <button
            className={`act-link ${action.infeasible ? "lead" : ""}`}
            onClick={() => handlers.onDismiss(action)}
            disabled={busy}
          >
            Dismiss
          </button>
        </div>
      </div>
    </li>
  );
}

/**
 * How long the pursuit has left, phrased as the solicitation closing — which is what the
 * deadline actually is. Driven by the server's `closes_in_days` so it can never contradict
 * the card's own reason line.
 */
function closesLabel(days?: number | null): { text: string; tone: string } | null {
  if (days == null) return null;
  if (days < 0) return { text: "Closed", tone: "overdue" };
  if (days === 0) return { text: "Closes today", tone: "overdue" };
  if (days === 1) return { text: "Closes tomorrow", tone: "overdue" };
  if (days <= 14) return { text: `Closes in ${days} days`, tone: "soon" };
  return { text: `Closes in ${days} days`, tone: "ok" };
}

/** The kind-specific control. This is the part that makes the card a task and not a link. */
function primary(a: ActionItem, h: ActionHandlers, busy?: boolean) {
  switch (a.kind) {
    case "analyze":
      return (
        <button className="act-btn" onClick={() => h.onAnalyze(a)} disabled={busy}>
          Analyse now
        </button>
      );
    case "decide":
      // Both answers, inline. The whole task is one of two clicks — sending the rep to the
      // record to find the same two buttons would be the old workflow with extra steps.
      return (
        <>
          <button className="act-btn" onClick={() => h.onDecide(a, "Bid")} disabled={busy}>
            Bid
          </button>
          <button className="act-btn ghost" onClick={() => h.onDecide(a, "No-Bid")} disabled={busy}>
            No-Bid
          </button>
        </>
      );
    case "approve_capture":
    case "retry_capture":
      return (
        <button className="act-btn" onClick={() => h.onApproveCapture(a)} disabled={busy}>
          {a.kind === "retry_capture" ? "Retry capture" : "Approve capture"}
        </button>
      );
    case "call":
      return (
        <button className="act-btn" onClick={() => h.onPrepCall(a)} disabled={busy}>
          Prep the call
        </button>
      );
    case "review_docs":
      return (
        <button className="act-btn" onClick={() => h.onOpenDocuments(a)} disabled={busy}>
          Open documents
        </button>
      );
    case "reply_mail":
      return (
        <button className="act-btn" onClick={() => h.onReplyMail(a)} disabled={busy}>
          Draft a reply
        </button>
      );
    case "submit":
    default:
      return (
        <button className="act-btn" onClick={() => h.onOpenOpportunity(a)} disabled={busy}>
          Open
        </button>
      );
  }
}
