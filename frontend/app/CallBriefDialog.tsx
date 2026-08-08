"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchCallBriefs,
  prepCall,
  type CallBriefDoc,
  type CallContact,
} from "@/lib/data";
import { useToastStore } from "@/lib/stores/toastStore";

const fmtDate = (s?: string | null) => {
  if (!s) return "";
  const d = new Date(s);
  return isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

/**
 * The call-prep dialog for one pursuit: a tab per contact, each with its own brief.
 *
 * LAZY BY DESIGN — a brief is an LLM run, so one only fires when the rep actually opens that
 * person's tab (and never again once it exists, unless they hit Re-prep). Opening a dialog
 * with eleven contacts therefore costs nothing until a tab is picked.
 *
 * Each brief is grounded in that contact's WHOLE ORGANISATION: the agent reads every thread
 * in the rep's mailbox with anyone at their email domain, which is why a person we've never
 * emailed can still have a useful brief.
 */
export default function CallBriefDialog({
  opportunityId,
  title,
  contacts,
  onClose,
}: {
  opportunityId: string;
  title: string;
  contacts: CallContact[];
  onClose: () => void;
}) {
  const [active, setActive] = useState<string | null>(contacts[0]?.email ?? null);
  const [briefs, setBriefs] = useState<Record<string, CallBriefDoc>>({});
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [loaded, setLoaded] = useState(false);
  const pushToast = useToastStore((s) => s.push);
  const aliveRef = useRef(true);
  // Tabs we've already fired a run for this session — stops the open-tab effect re-firing.
  const requested = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const r = await fetchCallBriefs(opportunityId);
      if (!aliveRef.current) return null;
      setBriefs(Object.fromEntries(r.briefs.map((b) => [b.contact_email, b])));
      setPending(new Set(r.pending));
      setLoaded(true);
      return r;
    } catch {
      if (aliveRef.current) setLoaded(true);
      return null;
    }
  }, [opportunityId]);

  useEffect(() => {
    aliveRef.current = true;
    load();
    return () => {
      aliveRef.current = false;
    };
  }, [load]);

  // Is there still work to wait for? Either the server says a run is in flight, or we fired
  // one whose brief hasn't landed yet — the second case matters because there is a window
  // (task just queued, mid-retry, or died) where the server reports nothing pending and no
  // brief exists. Treating that as "done" is what used to strand a tab on "Loading…".
  const needsPoll = useMemo(() => {
    if (pending.size > 0) return true;
    for (const email of requested.current) if (!briefs[email]) return true;
    return false;
  }, [pending, briefs]);

  // Read through a ref inside the interval so the schedule NEVER depends on changing state.
  // The old version keyed the timer off `pending`, and since every fetch built a new Set,
  // each poll tore down and rescheduled its own timer — and one empty response killed it.
  const needsPollRef = useRef(needsPoll);
  needsPollRef.current = needsPoll;

  useEffect(() => {
    const id = setInterval(() => {
      if (needsPollRef.current) load();
    }, 4000);
    return () => clearInterval(id);
  }, [load]);

  const run = useCallback(
    async (email: string) => {
      requested.current.add(email);
      setPending((p) => new Set(p).add(email));
      try {
        await prepCall(opportunityId, email);
      } catch {
        setPending((p) => {
          const n = new Set(p);
          n.delete(email);
          return n;
        });
        pushToast("Couldn't start the prep — is the backend + worker running?");
      }
    },
    [opportunityId, pushToast],
  );

  // THE lazy trigger: opening a tab with no brief and nothing in flight starts its run.
  useEffect(() => {
    if (!loaded || !active) return;
    if (briefs[active] || pending.has(active) || requested.current.has(active)) return;
    run(active);
  }, [loaded, active, briefs, pending, run]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const current = active ? briefs[active] : undefined;
  const preparing = !!active && pending.has(active) && !current;
  const activeContact = useMemo(
    () => contacts.find((c) => c.email === active),
    [contacts, active],
  );

  return (
    <div className="rev-scrim" onClick={onClose}>
      <div
        className="cbd"
        role="dialog"
        aria-modal="true"
        aria-label={`Call prep for ${title}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="rev-head">
          <div className="min-w-0">
            <div className="rev-email">{title}</div>
            <div className="rev-sub">
              Pick a person — each brief reads your mail with their whole organisation.
            </div>
          </div>
          <button className="sheet-btn" onClick={onClose} aria-label="Close" title="Close">
            ✕
          </button>
        </div>

        {contacts.length === 0 ? (
          <div className="cl-empty">
            No contacts on this pursuit yet. Add contacts on the opportunity and they&apos;ll
            show up here.
          </div>
        ) : (
          <div className="cbd-body">
            <div className="cbd-tabs">
              {contacts.map((c) => (
                <button
                  key={c.email}
                  className={`cbd-tab ${c.email === active ? "on" : ""}`}
                  onClick={() => setActive(c.email)}
                >
                  <span className="cbd-tab-name">{c.name || c.email}</span>
                  <span className="cbd-tab-sub">
                    {c.title || c.company || c.email}
                    {c.source === "poc" ? " · POC" : ""}
                  </span>
                  {pending.has(c.email) && !briefs[c.email] && <span className="cbd-dot" />}
                </button>
              ))}
            </div>

            <div className="cbd-pane">
              {preparing ? (
                <div className="cbd-loading">
                  <div className="cbd-big">Reading your mail with {activeContact?.company || "their organisation"}…</div>
                  <div className="cl-foot">
                    Searching every thread with anyone at their domain, then writing the brief.
                    This takes a moment.
                  </div>
                </div>
              ) : current ? (
                <Brief
                  doc={current}
                  name={activeContact?.name}
                  onReprep={() => run(current.contact_email)}
                  busy={pending.has(current.contact_email)}
                />
              ) : (
                <div className="cl-foot">Loading…</div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Brief({
  doc,
  name,
  onReprep,
  busy,
}: {
  doc: CallBriefDoc;
  name?: string | null;
  onReprep: () => void;
  busy: boolean;
}) {
  const b = doc.brief;
  return (
    <div className="cb-body">
      <div className="cbd-pane-head">
        <div>
          {/* The person is whoever's tab this is — we already know them, so the model is
              never asked to echo the name back. */}
          <div className="cb-org">{name || doc.contact_email}</div>
          <div className="cb-meta">
            {b.org_name}
            {doc.mail_count != null
              ? ` · ${doc.mail_count} email${doc.mail_count === 1 ? "" : "s"} with the org`
              : ""}
            {doc.refreshed_at ? ` · ${fmtDate(doc.refreshed_at)}` : ""}
          </div>
        </div>
        <button className="sel-link" onClick={onReprep} disabled={busy}>
          {busy ? "Refreshing…" : "Re-prep"}
        </button>
      </div>

      {/* The line the rep reads right before dialling. */}
      <div className="cb-ask cbd-approach">
        <span className="cb-h">How to talk to them</span> {b.approach}
      </div>

      <p className="cb-summary">{b.summary}</p>

      {b.relationship && (
        <div className="cb-sec">
          <div className="cb-h">Our history with them</div>
          <p className="cb-summary">{b.relationship}</p>
        </div>
      )}

      {b.org_context && (
        <div className="cb-sec">
          <div className="cb-h">What their org has in flight</div>
          <p className="cb-summary">{b.org_context}</p>
        </div>
      )}

      {b.talking_points?.length > 0 && (
        <div className="cb-sec">
          <div className="cb-h">Talking points</div>
          <ul>
            {b.talking_points.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {b.open_threads?.length > 0 && (
        <div className="cb-sec">
          <div className="cb-h">Open threads</div>
          <ul>
            {b.open_threads.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="cb-ask">
        <span className="cb-h">The ask</span> {b.suggested_ask}
      </div>
    </div>
  );
}
