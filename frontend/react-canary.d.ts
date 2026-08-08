// React's <ViewTransition> ships in the canary TYPE surface, not the stable one, so this
// reference is what makes TypeScript aware of it.
//
// The RUNTIME needs nothing extra: Next vendors a React build that exports ViewTransition
// (verified — `next/dist/compiled/react` exports it, the bare `react` package does not), and
// its react-dom already handles `view-transition-name`. Inside a Next app,
// `import { ViewTransition } from "react"` resolves to Next's copy.
//
// NOTE: there is no `experimental.viewTransition` flag in Next 16.3.0 — the feature graduated
// out of it. Setting one produces an "Unrecognized key" build warning and does nothing.
// (Next 16.2 and earlier did require it; that is where the stale advice comes from.)
//
// Requires Next >= 16 and React >= 19.2. See docs/frontend-implementation-plan.md §4.2.
/// <reference types="react/canary" />
