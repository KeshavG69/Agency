"use client";

import type { ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";

import { getQueryClient } from "@/lib/query-client";

/**
 * Wraps the tree in the shared QueryClient. Mount it inside `app/layout.tsx` (around
 * `AuthProvider`) when the first `useQuery` call lands.
 */
export default function QueryProvider({ children }: { children: ReactNode }) {
  // Deliberately NOT `useState(() => new QueryClient())`: getQueryClient already gives a
  // fresh client per server request and a browser singleton, so re-running this component
  // (Suspense retry, Fast Refresh) reuses the existing cache instead of discarding it.
  const queryClient = getQueryClient();

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
