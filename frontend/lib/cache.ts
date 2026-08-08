"use client";

// Invalidation, named by WHAT CHANGED — not by keys. Plan 5.4.
//
// Callers say `cache.opportunity(id)` after a mutation; they never think about which
// lists, pills or trails that record appears in. That mapping lives here, once, so
// adding a new derived query means editing this file instead of hunting every mutation.
//
// NO OPTIMISTIC UPDATES. There is deliberately no onMutate/setQueryData anywhere in this
// layer: rollback logic is the main source of "the UI said it saved and it didn't" bugs,
// and it buys nothing here because the server is the only place the verdict/fact rules
// are enforced. Immediacy comes from prefetch (lib/use-prefetch.ts) plus showing the
// in-flight value locally — `const shown = saving ? draft.trim() : value` — which is a
// display-only optimism with nothing to roll back.

import { useMemo } from "react";
import { useQueryClient, type QueryKey } from "@tanstack/react-query";

import { queryKeys } from "@/lib/queries";

/**
 * Which invalidations the caller waits on.
 *  - "all"    (default) — await everything; use it when the UI must be consistent before
 *              the next step, e.g. closing a dialog and returning to the list.
 *  - "record" — await only the record itself; the lists refresh behind you. Use it in an
 *              inline editor so the field's spinner clears the moment THAT record is
 *              fresh, instead of waiting on a 50-row page and a counts aggregation.
 * Both settle modes invalidate exactly the same keys. `settle` only changes what you AWAIT.
 */
export type Settle = "all" | "record";

export function useCollecctCache() {
  const qc = useQueryClient();

  // Memoised on the client: the returned object is a stable identity, so consumers can
  // safely put `cache` in a useEffect/useCallback dependency array without re-running on
  // every render.
  return useMemo(() => {
    const run = (record: QueryKey[], rest: QueryKey[], settle: Settle = "all") => {
      const awaited = settle === "all" ? [...record, ...rest] : record;
      const behind = settle === "all" ? [] : rest;
      for (const key of behind) void qc.invalidateQueries({ queryKey: key });
      return Promise.all(awaited.map((key) => qc.invalidateQueries({ queryKey: key })));
    };

    return {
      /**
       * One opportunity changed: decision flipped, assignment, capture approved, outreach
       * drafted. The row is in every paged list and every pill count, and the agent trail
       * gained an entry — but only the record itself is worth waiting for.
       */
      opportunity: (id: string, settle?: Settle) =>
        run(
          [queryKeys.opportunity(id)],
          [
            queryKeys.opportunities, // prefix: every page + the Bid sidebar
            ["counts"],
            ["agent-events", id], // the run just appended to this record's trail
          ],
          settle,
        ),

      /**
       * The SET of opportunities changed: a SAM pull, an Excel upload, a manual add, a
       * batch analyse. New agencies/NAICS can appear, so the filter vocabulary and the
       * calendar go too.
       */
      opportunities: (settle?: Settle) =>
        run(
          [queryKeys.opportunities],
          [["counts"], queryKeys.facets, ["posted-dates"]],
          settle,
        ),

      /** Only the pill counts are suspect (e.g. an in-flight analyse finished). */
      counts: () => run([["counts"]], []),

      /**
       * A suggestion on one contact was accepted or dismissed. Accepting promotes it to a
       * fact on that contact; either way it leaves the org-wide review queue.
       */
      contactFacts: (email: string, settle?: Settle) =>
        run([queryKeys.contactFacts(email)], [["suggestions"]], settle),

      /**
       * Decided from the global review queue, where the contact is incidental. Inverse of
       * contactFacts: the queue is what the user is looking at, so it settles first and
       * every contact panel catches up behind it.
       */
      suggestions: (settle?: Settle) =>
        run([["suggestions"]], [["contact-facts"]], settle),

      /** An agent run finished against one subject — refresh just its trail. */
      agentEvents: (subjectId: string) =>
        run([["agent-events", subjectId]], [queryKeys.queueHealth]),
    };
  }, [qc]);
}
