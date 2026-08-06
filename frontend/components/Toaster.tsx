"use client";

import { useToastStore } from "@/lib/stores/toastStore";

/**
 * Renders the global toast stack. Mounted once in the root layout, so a toast fired from
 * anywhere (any component, any store action) shows up here. Click a toast to dismiss it;
 * otherwise it auto-expires (see toastStore TTL).
 */
export default function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div className="toaster" role="status" aria-live="polite">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          className={`toast ${t.kind}`}
          onClick={() => dismiss(t.id)}
          title="Dismiss"
        >
          {t.message}
        </button>
      ))}
    </div>
  );
}
