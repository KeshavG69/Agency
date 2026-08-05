// The one QueryClient config for the whole app. Phase 5.1 of
// docs/frontend-implementation-plan.md.

import { defaultShouldDehydrateQuery, QueryClient } from "@tanstack/react-query";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      // 30s covers the window in which a user clicks around a pipeline row, opens the
      // detail pane and comes back — that round trip should never re-hit the API.
      queries: { staleTime: 30_000 },
      dehydrate: {
        // Dehydrate PENDING queries too. Without this, any server prefetch you did
        // not await is thrown away and re-fetched on the client — pure waste.
        //
        // Concretely: a Server Component that calls `void queryClient.prefetchQuery(...)`
        // and renders immediately leaves that query in state "pending" at serialization
        // time. `defaultShouldDehydrateQuery` only ships SUCCESS, so the in-flight
        // promise is dropped on the floor and the browser starts the same request from
        // zero — the prefetch cost us a request and bought nothing. With this override
        // the pending query is streamed and the client awaits the server's fetch.
        shouldDehydrateQuery: (q) =>
          defaultShouldDehydrateQuery(q) || q.state.status === "pending",
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient() {
  // A server render must never share a cache between requests — that would leak one
  // org's opportunities into another's HTML.
  if (typeof window === "undefined") return makeQueryClient();
  // In the browser the client must survive Suspense-triggered re-renders, so it is
  // created once and reused rather than rebuilt on each render pass.
  return (browserQueryClient ??= makeQueryClient());
}
