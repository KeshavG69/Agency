"use client";

import { useEffect, useRef, useState } from "react";
import {
  fetchMailTriage,
  markMailTriageRead,
  dismissMailTriage,
  draftMailTriageReply,
  createOutlookDraft,
  type MailTriageCard,
} from "@/lib/data";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

// One triage card's expand/reply state — kept local so a slow draft-reply generation on
// one card never disturbs the rest of the list.
function TriageRow({
  card,
  opportunityTitle,
  onOpen,
  onChanged,
}: {
  card: MailTriageCard;
  opportunityTitle?: string;
  onOpen: (opportunityId: string) => void;
  onChanged: (updated: MailTriageCard) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [reply, setReply] = useState(card.suggested_reply ?? "");
  const [creating, setCreating] = useState(false);
  const [createdLink, setCreatedLink] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Guards the draft-reply poll loop against setState after this row unmounts (e.g. the
  // card is dismissed, or the Dashboard is navigated away from, mid-poll).
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    setReply(card.suggested_reply ?? "");
  }, [card.suggested_reply]);

  const toggleExpand = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && card.status === "unread") {
      markMailTriageRead(card.id).catch(() => {});
      onChanged({ ...card, status: "read" });
    }
  };

  const onDraftReply = async () => {
    setErr(null);
    setDrafting(true);
    try {
      await draftMailTriageReply(card.id);
      // Poll the list for this card's suggested_reply to land (same pattern used
      // elsewhere for async agent tasks — short-interval poll, bounded attempts). Every
      // step checks aliveRef first — if the card was dismissed or the Dashboard was left
      // mid-poll, stop silently instead of updating state on an unmounted row.
      let sawResult = false;
      for (let i = 0; i < 30 && aliveRef.current; i++) {
        await sleep(2000);
        if (!aliveRef.current) break;
        const { cards } = await fetchMailTriage();
        if (!aliveRef.current) break;
        const fresh = cards.find((c) => c.id === card.id);
        if (fresh?.suggested_reply) {
          setReply(fresh.suggested_reply);
          onChanged(fresh);
          sawResult = true;
          break;
        }
        if (fresh?.reply_error) {
          // Retries exhausted server-side — stop polling instead of running out the clock.
          setErr("Couldn't generate a reply — try again.");
          sawResult = true;
          break;
        }
      }
      if (!sawResult && aliveRef.current) {
        setErr("Still working on a reply — try again in a moment.");
      }
    } catch {
      if (aliveRef.current) setErr("Couldn't generate a reply just now — try again.");
    } finally {
      if (aliveRef.current) setDrafting(false);
    }
  };

  const onCreateDraft = async () => {
    if (!reply.trim()) return;
    setErr(null);
    setCreating(true);
    try {
      const res = await createOutlookDraft(card.id, reply);
      setCreatedLink(res.web_link ?? null);
      onChanged({ ...card, status: "replied", suggested_reply: reply });
    } catch {
      setErr("Couldn't create the draft in Outlook — try again.");
    } finally {
      setCreating(false);
    }
  };

  const onDismiss = async () => {
    dismissMailTriage(card.id).catch(() => {});
    onChanged({ ...card, status: "dismissed" });
  };

  return (
    <div className={`triage-card ${card.status === "unread" ? "unread" : ""}`}>
      <button className="triage-head" onClick={toggleExpand}>
        <div className="triage-main">
          <div className="triage-from">
            {card.sender_name || card.sender_email}
            {opportunityTitle && <span className="triage-opp">· {opportunityTitle}</span>}
          </div>
          <div className="triage-subject">{card.subject || "(no subject)"}</div>
          {!expanded && <div className="triage-snippet">{card.snippet}</div>}
        </div>
        <div className="triage-meta">
          <span className="triage-time">{timeAgo(card.received_at)}</span>
          {card.status === "replied" && <span className="triage-badge">Replied</span>}
        </div>
      </button>

      {expanded && (
        <div className="triage-body">
          <div className="triage-snippet full">{card.snippet}</div>
          <div className="triage-actions">
            {card.opportunity_id && (
              <button className="triage-link" onClick={() => onOpen(card.opportunity_id)}>
                Open opportunity
              </button>
            )}
            {card.web_link && (
              <a className="triage-link" href={card.web_link} target="_blank" rel="noreferrer">
                Open in Outlook ↗
              </a>
            )}
            <button className="triage-link danger" onClick={onDismiss}>
              Dismiss
            </button>
          </div>

          {reply ? (
            <div className="triage-reply">
              <textarea
                className="triage-reply-box"
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                rows={5}
              />
              <div className="triage-reply-actions">
                <button className="btn ghost btn-sm" onClick={onDraftReply} disabled={drafting}>
                  {drafting ? "Regenerating…" : "Regenerate"}
                </button>
                <button className="btn primary btn-sm" onClick={onCreateDraft} disabled={creating}>
                  {creating ? "Creating…" : "Create draft in Outlook"}
                </button>
              </div>
              {createdLink && (
                <div className="triage-created">
                  Draft created —{" "}
                  <a href={createdLink} target="_blank" rel="noreferrer">
                    open it in Outlook to review + send ↗
                  </a>
                </div>
              )}
            </div>
          ) : (
            <button className="btn primary btn-sm" onClick={onDraftReply} disabled={drafting}>
              {drafting ? "Drafting…" : "Draft reply"}
            </button>
          )}
          {err && <div className="triage-err">{err}</div>}
        </div>
      )}
    </div>
  );
}

export default function MailTriagePanel({
  opportunityTitles,
  onOpenOpportunity,
}: {
  opportunityTitles: Record<string, string>;
  onOpenOpportunity: (opportunityId: string) => void;
}) {
  const [cards, setCards] = useState<MailTriageCard[] | null>(null);

  // The ref guard is for React's development double-invoke of effects: without it the
  // panel fired its fetch twice on every mount, which showed up as a duplicated
  // /api/mail-triage on the Dashboard's request waterfall.
  const fetched = useRef(false);
  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    let alive = true;
    fetchMailTriage()
      .then((r) => alive && setCards(r.cards))
      .catch(() => alive && setCards([]));
    return () => {
      alive = false;
    };
  }, []);

  const onChanged = (updated: MailTriageCard) => {
    setCards((prev) =>
      (prev ?? [])
        .map((c) => (c.id === updated.id ? updated : c))
        .filter((c) => c.status !== "dismissed"),
    );
  };

  if (cards === null) return null; // loading — nothing to show yet, avoids a layout flash
  if (cards.length === 0) return null; // nothing needs attention — don't clutter the Dashboard

  return (
    <div className="triage-panel">
      <div className="dash-col-head">
        <span>Needs attention</span>
        <span className="c">{cards.length}</span>
      </div>
      <div className="triage-list">
        {cards.map((c) => (
          <TriageRow
            key={c.id}
            card={c}
            opportunityTitle={opportunityTitles[c.opportunity_id]}
            onOpen={onOpenOpportunity}
            onChanged={onChanged}
          />
        ))}
      </div>
    </div>
  );
}
