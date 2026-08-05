"use client";

import { ViewTransition, addTransitionType, startTransition } from "react";

/**
 * Transition type -> view-transition-class. The values are the classes the CSS in
 * globals.css selects on (`::view-transition-new(.nav-lateral)` and friends).
 *
 * `default: "none"` is the point of the whole map: animation is OPT-IN. Any update that
 * carries no type renders instantly instead of inheriting whatever the last named
 * transition did. A silent no-op is the right failure mode for motion.
 */
const directional = {
  "nav-forward": "nav-forward",
  "nav-back": "nav-back",
  "nav-lateral": "nav-lateral",
  default: "none",
} as const;

/**
 * View-switch animation for a SINGLE-ROUTE app.
 *
 * WHY NOT THE USUAL SETUP: the reference implementation this is ported from puts
 * <ViewTransition> in each page.tsx and triggers it with `<Link transitionTypes={[...]}>`,
 * which works because every screen there is its own route. Collecct is ONE route — the
 * console swaps `view` in React state — so there is no navigation for `transitionTypes` to
 * attach to, and a wrapper that only fires on mount/unmount would never run. The only real
 * route changes here are the auth boundaries, crossed once a session.
 *
 * So the transition is driven from the state change instead (see `switchView` below). Wrap
 * the element whose CONTENT swaps — the view container, not the whole shell, or the top bar
 * and sidebar animate along with it.
 */
export function PageTransition({ children }: { children: React.ReactNode }) {
  return (
    <ViewTransition
      enter={directional}
      exit={directional}
      update={directional}
      default="none"
    >
      {children}
    </ViewTransition>
  );
}

/**
 * Run a view change inside a named view transition:
 *
 *   switchView(() => setView("pipeline"));
 *
 * Sibling tabs in a top bar imply no hierarchy, so "lateral" is the honest name: the CSS
 * gives it a fade plus a 12px rise and deliberately NO horizontal slide, which would imply a
 * direction that does not exist.
 *
 * Safe to call unconditionally — where View Transitions are unsupported, or the user prefers
 * reduced motion, the update simply applies without animating.
 */
export function switchView(update: () => void) {
  startTransition(() => {
    addTransitionType("nav-lateral");
    update();
  });
}
