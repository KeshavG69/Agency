// Shared presentational helpers used across the console + its extracted components.

export type DueTone = "overdue" | "soon" | "ok" | "far" | "none";

// Human, relative label for a response deadline — "Due tomorrow", "Due in 6 days",
// "Due Aug 14", "Due Aug 2028". Keyed on the DUE date, not the posted date.
export function dueLabel(s?: string | null): { text: string; tone: DueTone; days: number | null } {
  if (!s) return { text: "No deadline", tone: "none", days: null };
  const d = new Date(s);
  if (isNaN(d.getTime())) return { text: s, tone: "none", days: null };
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((due.getTime() - today.getTime()) / 86_400_000);
  if (days < 0) return { text: "Overdue", tone: "overdue", days };
  if (days === 0) return { text: "Due today", tone: "overdue", days };
  if (days === 1) return { text: "Due tomorrow", tone: "soon", days };
  if (days <= 14) return { text: `Due in ${days} days`, tone: "soon", days };
  const sameYear = due.getFullYear() === today.getFullYear();
  return {
    text: sameYear
      ? `Due ${due.toLocaleDateString("en-US", { month: "short", day: "numeric" })}`
      : `Due ${due.toLocaleDateString("en-US", { month: "short", year: "numeric" })}`,
    tone: sameYear ? "ok" : "far",
    days,
  };
}
