"use client";

import { useQuery } from "@tanstack/react-query";
import { FactRow } from "@/components/agent/FactSuggestion";
import { contactFactsQuery } from "@/lib/queries";

/** The fields worth showing on a contact card, in the order a rep reads them. */
const FIELDS = ["title", "seniority", "function", "phone"] as const;

/**
 * What enrichment learned about one contact, and the questions it still has.
 *
 * Placed on the contact card in the opportunity's Contacts tab — the moment a rep is
 * already deciding who to approach — rather than in a separate review queue. A suggestion
 * next to the person it describes gets settled in a second; the same suggestion in an inbox
 * is a chore for later.
 *
 * Renders NOTHING when there is neither a fact nor an open question: an empty labelled grid
 * on every contact would be worse than silence.
 */
export function ContactFacts({ email }: { email: string }) {
  const q = useQuery({
    ...contactFactsQuery(email),
    // A contact card is not a live surface. Facts change when a sweep or an agent runs,
    // which is minutes-to-days apart, so refetching on every tab focus is pure noise.
    refetchOnWindowFocus: false,
  });

  const facts = q.data?.facts ?? {};
  const suggestions = q.data?.suggestions ?? [];

  const rows = FIELDS.map((field) => ({
    field,
    value: facts[field],
    suggestion: suggestions.find((s) => s.field === field) ?? null,
  })).filter((r) => r.value || r.suggestion);

  // Never show a spinner here. The card is already useful without this block, and a
  // spinner per contact would make a list of ten people flicker on every open.
  if (rows.length === 0) return null;

  return (
    <div className="mt-2 border-t pt-2">
      {rows.map((r) => (
        <FactRow
          key={r.field}
          field={r.field}
          value={r.value}
          suggestion={r.suggestion}
        />
      ))}
    </div>
  );
}
