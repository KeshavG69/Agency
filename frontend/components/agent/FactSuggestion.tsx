"use client";

// A suggestion is settled UNDER THE FIELD IT IS ABOUT — not in a global inbox. Plan §7.3.
//
// This corrects the assumption baked into /api/intelligence/suggestions: a rep can confirm
// "VP, Business Development" in half a second while looking at the person, and cannot do it
// at all from a list of four hundred rows stripped of context. The org-wide queue stays
// useful for sweeping up; this is the surface that actually empties it.
//
// REQUIRES <QueryProvider> above it in the tree (components/QueryProvider.tsx).

import { useId, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { absoluteTime, primarySourceHost } from "@/lib/agent-trail";
import { useCollecctCache } from "@/lib/cache";
import { cn } from "@/lib/cn";
import { decideFact, type ContactFact, type FactField } from "@/lib/intelligence";

const FIELD_LABELS: Record<FactField, string> = {
  title: "Title",
  company: "Company",
  industry: "Industry",
  phone: "Phone",
  seniority: "Seniority",
  function: "Function",
  linkedin: "LinkedIn",
  website: "Website",
};

/** Falls back rather than throwing, so a field added to the backend list still reads. */
export function fieldLabel(field: string): string {
  const known = FIELD_LABELS[field as FactField];
  if (known) return known;
  const words = field.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : field;
}

// --- a settled fact ------------------------------------------------------------------

export interface AcceptedFactProps {
  value: string;
  /** The evidence sentence from models/evidence.py. Absent => rendered as plain text. */
  rationale?: string | null;
  /** Host of the first citable source behind it, if any. */
  sourceHost?: string;
  /** When the claim was last re-scored. */
  at?: string | null;
  className?: string;
}

/**
 * A fact with its provenance one hover (or one Tab) away.
 *
 * The dotted underline is a PROMISE that an explanation exists, so a fact with no rationale
 * renders as ordinary text instead. That case is real and not rare:
 * `/contacts/{email}/facts` projects `facts` down to `{field: value}`, so anything read
 * back on a page load arrives bare — only a fact accepted in this session carries the
 * sentence with it. Underlining those too would train reps to hover at nothing.
 */
export function AcceptedFact({
  value,
  rationale,
  sourceHost,
  at,
  className,
}: AcceptedFactProps) {
  const tooltipId = useId();
  const reason = (rationale || "").trim();
  const when = absoluteTime(at);
  const footer = [when, sourceHost].filter(Boolean).join(" · ");

  if (!reason) return <span className={className}>{value}</span>;

  return (
    <span className={cn("group relative inline-block", className)}>
      <span
        tabIndex={0}
        aria-describedby={tooltipId}
        className="cursor-help underline decoration-dotted decoration-from-font underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
      >
        {value}
      </span>
      <span
        role="tooltip"
        id={tooltipId}
        // pointer-events-none so the tooltip can never swallow the click meant for the row
        // underneath it. Positioned above: a fact sits in a stack of fields, and opening
        // downwards would cover the very field a rep is reading next.
        className="pointer-events-none absolute bottom-full left-0 z-30 mb-1.5 w-max max-w-[22rem] rounded-md border bg-popover p-2.5 text-xs leading-relaxed opacity-0 shadow-md transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {/* The opacity ladder: the claim is what you came for, the reasons justify it, the
            date and host are for the one rep in ten who wants to go and check. */}
        <span className="block font-medium opacity-100">{value}</span>
        <span className="mt-1 block opacity-80">{reason}</span>
        {footer && <span className="mt-1 block opacity-60">{footer}</span>}
      </span>
    </span>
  );
}

// --- the open question ---------------------------------------------------------------

export interface FactSuggestionProps {
  suggestion: ContactFact;
  /** Fired after the server has settled it. `fact` is the row as it now stands. */
  onDecided?: (fact: ContactFact, accepted: boolean) => void;
  className?: string;
}

/**
 * The inline accept / dismiss row that sits directly under a field.
 *
 * Both outcomes are one-way and enforced server-side — accepting makes the value
 * human-owned so no source may ever overwrite it, dismissing retires that value for good.
 * That is why they are buttons and nothing else: no hover, no focus, no keyboard shortcut
 * may reach this call by accident.
 */
export function FactSuggestion({ suggestion, onDecided, className }: FactSuggestionProps) {
  const cache = useCollecctCache();
  const [settled, setSettled] = useState<"accepted" | "dismissed" | null>(null);

  const decide = useMutation({
    mutationFn: (accept: boolean) => decideFact(suggestion.id, accept),
    onSuccess: async (result) => {
      setSettled(result.decided);
      onDecided?.(result.fact, result.decided === "accepted");
      // "record" settles the contact first and lets the org-wide queue catch up behind it:
      // the rep is looking at this person, not at the review list.
      await cache.contactFacts(suggestion.email, "record");
    },
  });

  if (settled) {
    return (
      <p className={cn("mt-1 text-xs text-muted-foreground", className)}>
        {settled === "accepted"
          ? "Accepted — held as fact from now on."
          : "Dismissed — this value will not be offered again."}
      </p>
    );
  }

  const label = fieldLabel(suggestion.field);
  const busy = decide.isPending;

  return (
    <div className={cn("mt-1 flex flex-wrap items-center gap-2 text-xs", className)}>
      <span className="text-muted-foreground">Suggested:</span>
      <span
        // The same sentence the tooltip shows once this is accepted — a rep should be able
        // to see WHY before they decide, not only after.
        title={suggestion.rationale}
        className="min-w-0 truncate text-foreground/80 underline decoration-dotted underline-offset-2"
      >
        {suggestion.value}
      </span>

      <span className="flex items-center gap-1">
        <button
          type="button"
          aria-label={`Accept ${label} “${suggestion.value}”`}
          onClick={() => decide.mutate(true)}
          disabled={busy}
          className="grid size-5 place-items-center rounded-sm border text-muted-foreground transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
        >
          <span aria-hidden>✓</span>
        </button>
        <button
          type="button"
          aria-label={`Dismiss ${label} “${suggestion.value}”`}
          onClick={() => decide.mutate(false)}
          disabled={busy}
          className="grid size-5 place-items-center rounded-sm border text-muted-foreground transition-colors hover:border-destructive hover:text-destructive disabled:opacity-50"
        >
          <span aria-hidden>✕</span>
        </button>
      </span>

      {decide.isError && (
        <span role="alert" className="text-destructive">
          Could not save that — try again.
        </span>
      )}
    </div>
  );
}

// --- the two together ----------------------------------------------------------------

export interface FactRowProps {
  field: string;
  label?: string;
  /** The settled value, from `facts[field]`. */
  value?: string | null;
  /** The open suggestion for this field, if the contact has one. */
  suggestion?: ContactFact | null;
  onDecided?: (fact: ContactFact, accepted: boolean) => void;
  className?: string;
}

/**
 * One labelled field: the fact if we hold one, the suggestion underneath if a human still
 * has to settle it.
 *
 * The accepted value appears here the instant the server confirms it, from the row the
 * mutation returned — display-only optimism with no cache write and nothing to roll back
 * (lib/cache.ts). The refetch it kicked off then quietly replaces it with the same value.
 */
export function FactRow({
  field,
  label,
  value,
  suggestion,
  onDecided,
  className,
}: FactRowProps) {
  const [justAccepted, setJustAccepted] = useState<ContactFact | null>(null);
  const shown = justAccepted?.value ?? value ?? "";

  return (
    <div
      className={cn(
        "flex flex-col gap-0.5 py-1.5 text-sm sm:grid sm:grid-cols-[8rem_1fr] sm:gap-x-3",
        className
      )}
    >
      <span className="pt-px text-xs uppercase tracking-wide text-muted-foreground">
        {label ?? fieldLabel(field)}
      </span>

      <div className="min-w-0">
        {shown ? (
          <AcceptedFact
            value={shown}
            rationale={justAccepted?.rationale}
            sourceHost={justAccepted ? primarySourceHost(justAccepted) : ""}
            at={justAccepted?.decided_at ?? justAccepted?.updated_at}
          />
        ) : (
          <span className="text-muted-foreground">—</span>
        )}

        {/* Once accepted the value has moved up into the field itself; leaving the row
            behind would ask the same question twice. */}
        {suggestion && !justAccepted && (
          <FactSuggestion
            suggestion={suggestion}
            onDecided={(fact, accepted) => {
              if (accepted) setJustAccepted(fact);
              onDecided?.(fact, accepted);
            }}
          />
        )}
      </div>
    </div>
  );
}

export default FactSuggestion;
