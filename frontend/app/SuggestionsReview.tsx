"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { FactRow } from "@/components/agent/FactSuggestion";
import { contactFactsQuery } from "@/lib/queries";
import { fetchSuggestionContacts, type SuggestionContact } from "@/lib/intelligence";

const PAGE = 50;

// The "confirm what the agents guessed" queue, grouped BY CONTACT. One row per person (not one
// per suggestion), so a contact with a title + seniority + function guess is a single entry.
// Clicking opens a dialog with ALL of that contact's suggestions to accept/dismiss together.
export default function SuggestionsReview() {
  const [items, setItems] = useState<SuggestionContact[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [done, setDone] = useState(false);
  const [openEmail, setOpenEmail] = useState<string | null>(null);
  const sentinel = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    fetchSuggestionContacts({ offset: 0, limit: PAGE })
      .then((p) => {
        if (!alive) return;
        setItems(p.contacts);
        setTotal(p.total ?? p.contacts.length);
        if (p.contacts.length < PAGE || (p.total ?? 0) <= p.contacts.length) setDone(true);
      })
      .catch(() => alive && setDone(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const loadMore = useCallback(() => {
    if (loading || done) return;
    setLoading(true);
    fetchSuggestionContacts({ offset: items.length, limit: PAGE })
      .then((p) => {
        setItems((prev) => [...prev, ...p.contacts]);
        if (p.contacts.length < PAGE) setDone(true);
      })
      .catch(() => setDone(true))
      .finally(() => setLoading(false));
  }, [loading, done, items.length]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { rootMargin: "300px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [loadMore]);

  // One suggestion settled for `email`: drop its count; remove the contact when it hits zero.
  const onSettled = useCallback((email: string) => {
    setItems((prev) =>
      prev.flatMap((c) => {
        if (c.email !== email) return [c];
        if (c.count <= 1) {
          setTotal((t) => (t == null ? t : Math.max(0, t - 1)));
          return [];
        }
        return [{ ...c, count: c.count - 1 }];
      }),
    );
  }, []);

  return (
    <div className="view-list">
      <div className="view-toolbar">
        <div className="view-count">
          {total != null && total > 0
            ? `${total.toLocaleString()} contact${total === 1 ? "" : "s"} to review`
            : ""}
        </div>
      </div>

      <div className="review-rows">
        {items.map((c) => (
          <button className="review-contact" key={c.email} onClick={() => setOpenEmail(c.email)}>
            <span className="rc-email">{c.email}</span>
            <span className="rc-count">
              {c.count} to review<span className="rc-arrow" aria-hidden>›</span>
            </span>
          </button>
        ))}

        {items.length === 0 && !loading && (
          <div className="cl-empty">Nothing to review — every suggestion is settled. 🎉</div>
        )}

        <div ref={sentinel} className="cl-sentinel" />
        <div className="cl-foot">
          {loading ? "Loading…" : done && items.length > 0 ? "All caught up" : ""}
        </div>
      </div>

      {openEmail && (
        <ContactReviewDialog
          email={openEmail}
          onClose={() => setOpenEmail(null)}
          onSettled={onSettled}
        />
      )}
    </div>
  );
}

// The per-contact dialog: every open suggestion for one person, each with accept / dismiss.
function ContactReviewDialog({
  email,
  onClose,
  onSettled,
}: {
  email: string;
  onClose: () => void;
  onSettled: (email: string) => void;
}) {
  const q = useQuery({ ...contactFactsQuery(email), refetchOnWindowFocus: false });
  const facts = q.data?.facts ?? {};
  const suggestions = q.data?.suggestions ?? [];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="rev-scrim" onClick={onClose}>
      <div
        className="rev-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Review ${email}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="rev-head">
          <div className="min-w-0">
            <div className="rev-email">{email}</div>
            <div className="rev-sub">
              Confirm what enrichment found — accept keeps it as fact, dismiss drops it for good.
            </div>
          </div>
          <button className="sheet-btn" onClick={onClose} aria-label="Close" title="Close">
            ✕
          </button>
        </div>

        <div className="rev-body">
          {q.isPending ? (
            <div className="cl-foot">Loading…</div>
          ) : suggestions.length === 0 ? (
            <div className="cl-empty">All settled for this contact. 🎉</div>
          ) : (
            suggestions.map((s) => (
              <FactRow
                key={`${s.field}-${s.id}`}
                field={s.field}
                value={facts[s.field]}
                suggestion={s}
                onDecided={() => onSettled(email)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
