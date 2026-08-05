"use client";

// Hover + focus prefetch. Plan 5.3 — ten lines for the biggest perceived-speed win in the
// app: by the time the click lands, the detail pane's data is already in cache.

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { opportunityQuery, sharePointFilesQuery } from "@/lib/queries";

/**
 * Warm the full opportunity record.
 *
 * Wire it to BOTH pointer and keyboard entry, so keyboard users get the same speed:
 *   <tr onMouseEnter={() => prefetch(o.id)} onFocus={() => prefetch(o.id)}>
 *
 * prefetchQuery honours staleTime, so re-entering a row inside the 30s window is free —
 * no debounce or "already fetched" bookkeeping needed at the call site.
 */
export function usePrefetchOpportunity() {
  const qc = useQueryClient();

  return useCallback(
    (id: string) => {
      if (!id) return;
      // Fire-and-forget: a prefetch that fails is not an error the user should ever see —
      // the real fetch on click will surface it properly.
      void qc.prefetchQuery(opportunityQuery(id));
    },
    [qc],
  );
}

/**
 * Warm the Bid's live SharePoint listing. Separate hook, and deliberately NOT folded into
 * the row hover: this one hits Microsoft Graph, so it is worth firing only where the user
 * has already committed to the record — hovering the Documents tab, not the list row.
 */
export function usePrefetchSharePointFiles() {
  const qc = useQueryClient();

  return useCallback(
    (id: string) => {
      if (!id) return;
      void qc.prefetchQuery(sharePointFilesQuery(id));
    },
    [qc],
  );
}
