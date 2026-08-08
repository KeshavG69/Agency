import { create } from "zustand";

/**
 * App-wide transient notifications. Replaces the hand-rolled `err` state + fixed
 * `<div className="toast">` that a few components each carried their own copy of. Any
 * component can now fire one without wiring its own state or markup:
 *
 *   useToastStore.getState().push("Couldn't save — try again.");
 *   // or, outside React:  toast("Saved.", "success");
 *
 * A single <Toaster> (mounted in the root layout) renders the stack. NOT for inline form
 * validation or a pane's "failed to load" message — those stay local so they render in place.
 */
export type ToastKind = "error" | "success" | "info";

export interface Toast {
  id: number;
  message: string;
  kind: ToastKind;
}

interface ToastStore {
  toasts: Toast[];
  push: (message: string, kind?: ToastKind) => number;
  dismiss: (id: number) => void;
  clear: () => void;
}

// Monotonic ids from a module counter — stable keys without pulling in Date.now/random.
let nextId = 1;
const TTL_MS = 4500;

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (message, kind = "info") => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, message, kind }] }));
    // Auto-dismiss. The <Toaster> also allows click-to-dismiss; this is the fallback so a
    // toast never lingers if the user ignores it.
    setTimeout(() => useToastStore.getState().dismiss(id), TTL_MS);
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  clear: () => set({ toasts: [] }),
}));

/** Fire a toast from anywhere, including non-React code (event callbacks, catch blocks). */
export const toast = (message: string, kind?: ToastKind) =>
  useToastStore.getState().push(message, kind);
