// Resolve the backend base URL.
//
// Priority:
//   1. RUNTIME value injected via /__env.js (window.__ENV.API_BASE) — written by the Docker
//      entrypoint from the container's env, so the SAME built image works in any environment
//      without rebuilding.
//   2. Build-time NEXT_PUBLIC_API_BASE (inlined by Next at build).
//   3. localhost:8000 (local dev).
// Always returns a non-empty string.

declare global {
  interface Window {
    __ENV?: { API_BASE?: string };
  }
}

export function getApiBase(): string {
  if (typeof window !== "undefined" && window.__ENV?.API_BASE) {
    return window.__ENV.API_BASE;
  }
  return process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
}
