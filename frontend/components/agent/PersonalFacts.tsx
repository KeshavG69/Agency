"use client";

// The personal / relationship lane on a contact — the human thread the relationship engine
// pulls on (golf, family, key dates). Distinct from ContactFacts' professional block because
// these are MULTI-VALUED and hand-entered: a contact can hold several interests, and "we
// played golf" almost never lands in a mailbox, so a rep types it here.
//
// Three parts: the settled facts (chips, each removable), the mail-scanned suggestions
// waiting to be confirmed, and a small add-a-note form. Every write goes through the same
// facts store as the professional lane, so ownership + dismissal rules are identical.

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { fieldLabel } from "@/components/agent/FactSuggestion";
import { useCollecctCache } from "@/lib/cache";
import { cn } from "@/lib/cn";
import {
  addManualFact,
  decideFact,
  PERSONAL_FACT_FIELDS,
  type ContactFact,
  type ContactFactsResponse,
  type PersonalFactField,
} from "@/lib/intelligence";

const PLACEHOLDER: Record<PersonalFactField, string> = {
  interests: "e.g. golf, cycling",
  shared_activity: "e.g. played golf 2026-08-03",
  family: "e.g. daughter at Purdue",
  key_date: "e.g. birthday June 12",
  personal_note: "anything worth remembering",
};

export function PersonalFacts({
  email,
  personal,
  suggestions,
}: {
  email: string;
  personal: ContactFactsResponse["personal"];
  suggestions: ContactFact[];
}) {
  const cache = useCollecctCache();
  const [field, setField] = useState<PersonalFactField>("interests");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = () => cache.contactFacts(email, "record");

  const add = useMutation({
    mutationFn: () => addManualFact(email, field, value.trim()),
    onSuccess: async () => {
      setValue("");
      setError(null);
      await refresh();
    },
    onError: () => setError("Couldn't save that — try again."),
  });

  const remove = useMutation({
    mutationFn: (factId: string) => decideFact(factId, false),
    onSuccess: () => refresh(),
  });

  // Only the personal-lane suggestions belong here; professional ones stay in ContactFacts.
  const personalFields = new Set<string>(PERSONAL_FACT_FIELDS);
  const openSuggestions = suggestions.filter((s) => personalFields.has(s.field));

  const grouped = PERSONAL_FACT_FIELDS.map((f) => ({
    field: f,
    values: personal?.[f] ?? [],
  })).filter((g) => g.values.length > 0);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim() && !add.isPending) add.mutate();
  };

  return (
    <div className="mt-3 border-t pt-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Personal
      </p>

      {grouped.length === 0 && openSuggestions.length === 0 && (
        <p className="mb-2 text-xs text-muted-foreground">
          Nothing yet — note what keeps this relationship warm.
        </p>
      )}

      {/* Settled facts, grouped by field, each value a removable chip. */}
      {grouped.map((g) => (
        <div key={g.field} className="mb-2 flex flex-col gap-1 sm:grid sm:grid-cols-[8rem_1fr] sm:gap-x-3">
          <span className="pt-1 text-xs uppercase tracking-wide text-muted-foreground">
            {fieldLabel(g.field)}
          </span>
          <div className="flex min-w-0 flex-wrap gap-1.5">
            {g.values.map((v) => (
              <span
                key={v.id}
                className="group inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5 text-xs text-foreground/90"
                title={v.decided_by ? `Added by ${v.decided_by}` : undefined}
              >
                {v.value}
                <button
                  type="button"
                  aria-label={`Remove “${v.value}”`}
                  onClick={() => remove.mutate(v.id)}
                  disabled={remove.isPending}
                  className="grid size-3.5 place-items-center rounded-full text-muted-foreground opacity-60 transition hover:text-destructive hover:opacity-100 disabled:opacity-30"
                >
                  <span aria-hidden>×</span>
                </button>
              </span>
            ))}
          </div>
        </div>
      ))}

      {/* Mail-scanned suggestions — confirm or wave off. Reuses the same one-click decide. */}
      {openSuggestions.length > 0 && (
        <div className="mb-2 space-y-1">
          {openSuggestions.map((s) => (
            <PersonalSuggestionRow key={s.id} suggestion={s} onDecided={refresh} />
          ))}
        </div>
      )}

      {/* Add a note by hand — the only reliable source for the offline stuff. */}
      <form onSubmit={submit} className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
        <select
          value={field}
          onChange={(e) => setField(e.target.value as PersonalFactField)}
          className="rounded-sm border bg-background px-1.5 py-1 text-xs text-foreground"
          aria-label="Personal fact type"
        >
          {PERSONAL_FACT_FIELDS.map((f) => (
            <option key={f} value={f}>
              {fieldLabel(f)}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={PLACEHOLDER[field]}
          className="min-w-0 flex-1 rounded-sm border bg-background px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground/70"
          aria-label={`New ${fieldLabel(field)}`}
        />
        <button
          type="submit"
          disabled={!value.trim() || add.isPending}
          className="rounded-sm border px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-bid-ink disabled:opacity-50"
        >
          {add.isPending ? "Adding…" : "Add"}
        </button>
        {error && (
          <span role="alert" className="text-destructive">
            {error}
          </span>
        )}
      </form>
    </div>
  );
}

/** A personal suggestion the mail scan proposed — accept (make it a fact) or dismiss. */
function PersonalSuggestionRow({
  suggestion,
  onDecided,
}: {
  suggestion: ContactFact;
  onDecided: () => void;
}) {
  const [settled, setSettled] = useState<"accepted" | "dismissed" | null>(null);
  const decide = useMutation({
    mutationFn: (accept: boolean) => decideFact(suggestion.id, accept),
    onSuccess: (r) => {
      setSettled(r.decided);
      onDecided();
    },
  });

  if (settled) {
    return (
      <p className="text-xs text-muted-foreground">
        {settled === "accepted" ? "Added." : "Dismissed."}
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">{fieldLabel(suggestion.field)} suggested:</span>
      <span
        title={suggestion.rationale}
        className={cn(
          "min-w-0 truncate text-foreground/80 underline decoration-dotted underline-offset-2",
        )}
      >
        {suggestion.value}
      </span>
      <span className="flex items-center gap-1">
        <button
          type="button"
          aria-label={`Accept “${suggestion.value}”`}
          onClick={() => decide.mutate(true)}
          disabled={decide.isPending}
          className="grid size-5 place-items-center rounded-sm border text-muted-foreground transition-colors hover:border-primary hover:text-bid-ink disabled:opacity-50"
        >
          <span aria-hidden>✓</span>
        </button>
        <button
          type="button"
          aria-label={`Dismiss “${suggestion.value}”`}
          onClick={() => decide.mutate(false)}
          disabled={decide.isPending}
          className="grid size-5 place-items-center rounded-sm border text-muted-foreground transition-colors hover:border-destructive hover:text-destructive disabled:opacity-50"
        >
          <span aria-hidden>✕</span>
        </button>
      </span>
    </div>
  );
}

export default PersonalFacts;
