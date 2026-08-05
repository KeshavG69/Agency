// Typed query-option factories — one per endpoint that actually exists.
//
// Everything here is `queryOptions()` rather than a bare object so the key, the fetcher's
// return type and the options travel together: `useQuery(opportunityQuery(id))`,
// `qc.prefetchQuery(opportunityQuery(id))` and `qc.setQueryData(...)` all infer the same
// `Opportunity` with no generics at the call site.
//
// The HTTP lives in lib/data.ts and lib/intelligence.ts; this file only names and
// configures the queries. Never inline a URL here — a path duplicated in two places is a
// path that drifts.

import { queryOptions } from "@tanstack/react-query";

import {
  fetchBids,
  fetchFacets,
  fetchOpportunity,
  fetchOpportunityCounts,
  fetchOpportunityPage,
  fetchOpportunitySharePointFiles,
  fetchPostedDates,
  type OpportunityPage,
  type PipelineParams,
} from "@/lib/data";
import {
  fetchAgentEvents,
  fetchContactFacts,
  fetchQueueHealth,
  fetchSuggestions,
} from "@/lib/intelligence";

/** Everything fetchOpportunityPage accepts, named so callers can hold it in state. */
export type PipelineListInput = PipelineParams & {
  offset?: number;
  limit?: number;
  withCounts?: boolean;
};

/**
 * The key vocabulary. lib/cache.ts invalidates by the PREFIXES declared here, so a new
 * query only participates in invalidation if its key starts with one of them.
 *
 * Note the array-valued filters (agencies/naics/setAsides): react-query hashes object
 * keys order-independently but NOT array elements, so callers must keep those arrays in a
 * stable order or the same filter set will occupy two cache entries.
 */
export const queryKeys = {
  opportunities: ["opportunities"] as const,
  opportunityList: (input: PipelineListInput) => ["opportunities", "page", input] as const,
  bids: ["opportunities", "bids"] as const,
  opportunity: (id: string) => ["opportunity", id] as const,
  sharePointFiles: (id: string) => ["opportunity", id, "sharepoint-files"] as const,
  counts: (p: PipelineParams) => ["counts", p] as const,
  facets: ["facets"] as const,
  postedDates: (p: PipelineParams) => ["posted-dates", p] as const,
  contactFacts: (email: string) => ["contact-facts", email] as const,
  suggestions: (p: { offset?: number; limit?: number }) => ["suggestions", p] as const,
  agentEvents: (subjectId: string, limit: number) => ["agent-events", subjectId, limit] as const,
  queueHealth: ["queue-health"] as const,
} as const;

// ---- pipeline ----------------------------------------------------------------------

/**
 * One page of the pipeline list (slim rows; the detail pane fetches the full record).
 *
 * `placeholderData: previous` is the loading rule from plan 5.2 baked in at the source:
 * a refetch (new filter, next page, background refresh) keeps the old rows on screen and
 * only flips `isFetching`, so the list never blanks out under the user. Callers must not
 * gate rendering on `isFetching` — only on `isPending`.
 */
export function opportunityListQuery(input: PipelineListInput = {}) {
  return queryOptions({
    queryKey: queryKeys.opportunityList(input),
    queryFn: () => fetchOpportunityPage(input),
    placeholderData: (previous: OpportunityPage | undefined) => previous,
  });
}

/** The org's Bid set for the sidebar. Pages internally, so it is one query, not N. */
export function bidsQuery() {
  return queryOptions({
    queryKey: queryKeys.bids,
    queryFn: fetchBids,
  });
}

/** The FULL enriched record for the detail pane — also the target of the hover prefetch. */
export function opportunityQuery(id: string) {
  return queryOptions({
    queryKey: queryKeys.opportunity(id),
    queryFn: () => fetchOpportunity(id),
    enabled: Boolean(id),
  });
}

/**
 * The Documents tab's LIVE read of the Bid's SharePoint folder. Split from
 * opportunityQuery because it round-trips to Graph and is an order of magnitude slower —
 * folding it in would hold the whole detail pane at the speed of SharePoint.
 */
export function sharePointFilesQuery(id: string) {
  return queryOptions({
    queryKey: queryKeys.sharePointFiles(id),
    queryFn: () => fetchOpportunitySharePointFiles(id),
    enabled: Boolean(id),
  });
}

/**
 * The status-pill counts. Only for callers that do NOT already get them inline from
 * `opportunityListQuery` (fetchOpportunityPage returns `counts` unless withCounts:false) —
 * asking for both is the two-request pattern the paged endpoint was built to replace.
 */
export function opportunityCountsQuery(p: PipelineParams = {}) {
  return queryOptions({
    queryKey: queryKeys.counts(p),
    queryFn: () => fetchOpportunityCounts(p),
    placeholderData: (previous) => previous, // pills must not blank between keystrokes
  });
}

/** Filter-bar vocabulary. Changes only when new opportunities land, hence the long stale time. */
export function facetsQuery() {
  return queryOptions({
    queryKey: queryKeys.facets,
    queryFn: fetchFacets,
    staleTime: 5 * 60_000,
  });
}

/** Which days have postings, for the calendar strip. */
export function postedDatesQuery(p: PipelineParams = {}) {
  return queryOptions({
    queryKey: queryKeys.postedDates(p),
    queryFn: () => fetchPostedDates(p),
    placeholderData: (previous) => previous,
  });
}

// ---- intelligence ------------------------------------------------------------------

/** Settled facts + open suggestions for one contact. Suggestions render UNDER the field. */
export function contactFactsQuery(email: string) {
  return queryOptions({
    queryKey: queryKeys.contactFacts(email),
    queryFn: () => fetchContactFacts(email),
    enabled: Boolean(email),
  });
}

/** The org-wide review queue, strongest first. Backend caps `limit` at 100. */
export function suggestionsQuery(p: { offset?: number; limit?: number } = {}) {
  return queryOptions({
    queryKey: queryKeys.suggestions(p),
    queryFn: () => fetchSuggestions(p),
    placeholderData: (previous) => previous,
  });
}

/**
 * The agent trail for one record. Append-only server-side, so a refetch can only ever
 * grow the list — safe to poll while a run is in flight (plan 7.1) without the trail
 * ever appearing to lose entries.
 */
export function agentEventsQuery(subjectId: string, limit = 200) {
  return queryOptions({
    queryKey: queryKeys.agentEvents(subjectId, limit),
    queryFn: () => fetchAgentEvents(subjectId, limit),
    enabled: Boolean(subjectId),
  });
}

/** Background-queue depth. Short stale time: a stuck queue is only useful news while it is news. */
export function queueHealthQuery() {
  return queryOptions({
    queryKey: queryKeys.queueHealth,
    queryFn: fetchQueueHealth,
    staleTime: 15_000,
  });
}
