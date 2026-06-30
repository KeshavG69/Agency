// Runtime environment, served at /__env.js. The Docker entrypoint OVERWRITES this at
// container start from the container's env (API_BASE). An empty value means the app falls
// back to the build-time NEXT_PUBLIC_API_BASE (or localhost) — e.g. during `next dev`.
window.__ENV = { API_BASE: "" };
