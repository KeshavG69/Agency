"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { sendMail } from "@/lib/data";
import { decideNudge, fetchNudge } from "@/lib/nudges";
import { useToastStore } from "@/lib/stores/toastStore";

/**
 * The relationship nudge, opened from a Today card.
 *
 * The engine has already done the work — decided WHO to reach out to, WHY now, and drafted
 * WHAT to say. This is the rep's one glance before it goes: the drafted message (editable),
 * the evidence that fired it, and two ways out. "Approve & send" is the one-tap the whole
 * feature is built around; it posts to the SAME human-send path the mail artifact uses, so
 * nothing here sends without an explicit human click — the product's hard rule.
 *
 * Dismiss retires the nudge for good (the sweep never reopens a decided one). Both outcomes
 * close the paired Today card server-side; `onResolved` just refreshes the plan.
 */
export default function NudgeDialog({
  nudgeId,
  onClose,
  onResolved,
}: {
  nudgeId: string;
  onClose: () => void;
  onResolved: () => void;
}) {
  const pushToast = useToastStore((s) => s.push);
  const q = useQuery({ queryKey: ["nudge", nudgeId], queryFn: () => fetchNudge(nudgeId) });
  const nudge = q.data;

  // Seed the editable draft once the nudge loads; the rep can tweak before sending.
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (nudge && !seeded) {
      setSubject(nudge.subject ?? "");
      setBody(nudge.body ?? "");
      setSeeded(true);
    }
  }, [nudge, seeded]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const contactLabel = useMemo(() => {
    if (!nudge) return "";
    return nudge.contact_name
      ? `${nudge.contact_name}${nudge.contact_company ? ` · ${nudge.contact_company}` : ""}`
      : nudge.contact_email;
  }, [nudge]);

  // Approve = send (human-initiated), then record the decision. Send must succeed first —
  // recording "approved" on a send that failed would drop the card on a message never sent.
  const approve = useMutation({
    mutationFn: async () => {
      await sendMail({
        to: nudge!.contact_email,
        to_name: nudge!.contact_name ?? undefined,
        subject: subject.trim(),
        body: body.trim(),
      });
      await decideNudge(nudgeId, "approve");
    },
    onSuccess: () => {
      pushToast("Sent — nice touch.", "success");
      onResolved();
      onClose();
    },
    // Show the server's reason: `sendMail` rethrows the API's `detail`, which says exactly what
    // to fix (e.g. an unfilled "[Your Name]" placeholder). A generic toast left the rep stuck.
    onError: (e) =>
      pushToast(e instanceof Error && e.message ? e.message : "Couldn't send that — nothing was sent."),
  });

  const dismiss = useMutation({
    mutationFn: () => decideNudge(nudgeId, "dismiss"),
    onSuccess: () => {
      pushToast("Dismissed.");
      onResolved();
      onClose();
    },
    onError: () => pushToast("Couldn't dismiss that — try again."),
  });

  const busy = approve.isPending || dismiss.isPending;
  const canSend = seeded && subject.trim().length > 0 && body.trim().length > 0 && !busy;

  return (
    <div className="rev-scrim" onClick={onClose}>
      <div
        className="nudge-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Relationship outreach"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="rev-head">
          <div className="min-w-0">
            <div className="rev-email">{contactLabel || "Loading…"}</div>
            {nudge && (
              <div className="rev-sub">
                {nudge.kind === "relationship_personal" ? "Personal note" : "Reconnect"}
                {nudge.contact_email ? ` · ${nudge.contact_email}` : ""}
              </div>
            )}
          </div>
          <button className="sheet-btn" onClick={onClose} aria-label="Close" title="Close">
            ✕
          </button>
        </div>

        {q.isPending ? (
          <div className="nudge-body">
            <p className="text-sm text-muted-foreground">Loading the draft…</p>
          </div>
        ) : !nudge ? (
          <div className="nudge-body">
            <p className="text-sm text-muted-foreground">Couldn&apos;t load this nudge.</p>
          </div>
        ) : (
          <div className="nudge-body">
            {/* Why this, why now — the machine-assembled evidence, shown before the draft so
                the rep sees the reason, not just the message. */}
            <div className="nudge-why">
              <span className="nudge-why-label">Why now</span>
              <p className="nudge-reason">{nudge.reason}</p>
              {/* The last exchange, called out on its own line: it is the thing a rep
                  actually scans before deciding whether this is worth sending. */}
              {nudge.last_subject && (
                <p className="nudge-last">
                  <span className="nudge-last-label">Last exchange</span>
                  <span className="nudge-last-subject">“{nudge.last_subject}”</span>
                  {nudge.last_contact && (
                    <span className="nudge-last-date">{nudge.last_contact}</span>
                  )}
                </p>
              )}
              {nudge.drew_on.length > 0 && (
                <div className="nudge-chips">
                  {nudge.drew_on.map((d, i) => (
                    <span key={i} className="nudge-chip">
                      {d}
                    </span>
                  ))}
                </div>
              )}
            </div>

            <label className="nudge-field">
              <span className="nudge-field-label">Subject</span>
              <input
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                disabled={busy}
                className="nudge-input"
              />
            </label>

            <label className="nudge-field">
              <span className="nudge-field-label">Message</span>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                disabled={busy}
                rows={9}
                className="nudge-textarea"
              />
            </label>

            <p className="nudge-note">
              Sends from your Outlook to {nudge.contact_email}. Nothing is sent until you click.
            </p>

            <div className="nudge-actions">
              <button
                className="act-btn"
                onClick={() => approve.mutate()}
                disabled={!canSend}
              >
                {approve.isPending ? "Sending…" : "Approve & send"}
              </button>
              <button
                className="act-link"
                onClick={() => dismiss.mutate()}
                disabled={busy}
              >
                {dismiss.isPending ? "Dismissing…" : "Dismiss"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
