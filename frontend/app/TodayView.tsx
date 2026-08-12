"use client";

import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import ActionCard, { type ActionHandlers } from "@/app/ActionCard";
import {
  analyzeSelected,
  approveCapture,
  closeAction,
  replanActions,
  setDecision,
  snoozeAction,
  type ActionItem,
  type BidDecision,
  type TodayPlan,
} from "@/lib/data";
import { todayPlanQuery, queryKeys } from "@/lib/queries";
import { useToastStore } from "@/lib/stores/toastStore";

/**
 * Today — the landing view.
 *
 * WHAT IT IS FOR. The Pipeline answers "what opportunities exist"; this answers "what do I
 * do". Every row is a verb, scheduled backwards from its pursuit's response deadline, so a
 * bid closing in three days puts its whole remaining chain here and one closing in a month
 * contributes nothing until its turn. Forty rows is fine — forty rows of tasks is a day's
 * work plan. Forty rows of records is the thing this replaces.
 *
 * The count is not the point; the unit is.
 *
 * Next week's work is a COLLAPSED strip of counts at the bottom, never cards. The moment
 * today's page contains tomorrow's work it stops being today's page.
 */

export default function TodayView({
  onOpenOpportunity,
  onPrepCall,
  onOpenDocuments,
  onOpenMail,
}: {
  /** Open the pursuit's detail sheet in the Pipeline, optionally on a given tab. */
  onOpenOpportunity: (opportunityId: string, tab?: "documents") => void;
  /** Open the Call Plan brief dialog for this pursuit. */
  onPrepCall: (opportunityId: string) => void;
  onOpenDocuments: (opportunityId: string) => void;
  /** Jump to the mail-triage card on the Dashboard. */
  onOpenMail: () => void;
}) {
  // The org-wide view had a Mine/Everyone toggle; the Everyone side was removed, so every
  // read here is the acting user's own plan. Held as one constant because three places need
  // to agree on it — the fetch, the query key, and the optimistic close-out's cache write.
  // The endpoint still accepts ?scope=org for an admin; nothing in the UI asks for it.
  const scope = "mine" as const;
  const [busyId, setBusyId] = useState<string | null>(null);
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);

  const planQ = useQuery(todayPlanQuery(scope));
  const plan = planQ.data;

  /**
   * Optimistic close-out. Ticking something off has to feel like ticking something off, so
   * the card leaves immediately and only comes back if the server disagrees. `onMutate`
   * snapshots the whole plan, which is what the rollback restores.
   */
  const close = useMutation({
    mutationFn: ({ action, run }: { action: ActionItem; run: () => Promise<void> }) => run(),
    onMutate: async ({ action }) => {
      const key = queryKeys.todayPlan(scope);
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<TodayPlan>(key);
      qc.setQueryData<TodayPlan>(key, (old) =>
        !old
          ? old
          : {
              ...old,
              overdue: old.overdue.filter((a) => a.id !== action.id),
              today: old.today.filter((a) => a.id !== action.id),
              counts: {
                ...old.counts,
                overdue: old.overdue.filter((a) => a.id !== action.id).length,
                today: old.today.filter((a) => a.id !== action.id).length,
              },
            },
      );
      return { previous, key };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(ctx.key, ctx.previous);
      pushToast("Couldn't update that — putting it back.");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["actions"] }),
  });

  /**
   * A primary control (analyse / decide / approve capture) does the real work AND closes the
   * card. The card is closed here rather than left for the planner because the planner runs
   * on a debounce — waiting for it would leave a card the rep just actioned sitting on screen
   * looking untouched.
   */
  const act = useCallback(
    async (action: ActionItem, work: () => Promise<unknown>, done: string) => {
      setBusyId(action.id);
      try {
        await work();
        await closeAction(action.id, "done");
        pushToast(done, "success");
      } catch {
        pushToast("That didn't go through — nothing has changed.");
      } finally {
        setBusyId(null);
        qc.invalidateQueries({ queryKey: ["actions"] });
        qc.invalidateQueries({ queryKey: queryKeys.opportunities });
      }
    },
    [pushToast, qc],
  );

  const handlers: ActionHandlers = useMemo(
    () => ({
      onDone: (a) => close.mutate({ action: a, run: () => closeAction(a.id, "done") }),
      onDismiss: (a) => close.mutate({ action: a, run: () => closeAction(a.id, "dismiss") }),
      onSnooze: (a, days) => close.mutate({ action: a, run: () => snoozeAction(a.id, days) }),

      onAnalyze: (a) =>
        a.opportunity_id &&
        act(a, () => analyzeSelected([a.opportunity_id!]), "Sent to the Analyst."),
      onDecide: (a, decision: BidDecision) =>
        a.opportunity_id &&
        act(a, () => setDecision(a.opportunity_id!, decision), `Marked ${decision}.`),
      onApproveCapture: (a) =>
        a.opportunity_id &&
        act(a, () => approveCapture(a.opportunity_id!), "Capture is running."),

      // These open a tool rather than firing an endpoint, so the card STAYS — the work is
      // not finished until the rep says it is. Closing it on open would be a lie.
      onPrepCall: (a) => a.opportunity_id && onPrepCall(a.opportunity_id),
      onOpenDocuments: (a) => a.opportunity_id && onOpenDocuments(a.opportunity_id),
      onOpenOpportunity: (a) => a.opportunity_id && onOpenOpportunity(a.opportunity_id),
      onReplyMail: () => onOpenMail(),
    }),
    [act, close, onPrepCall, onOpenDocuments, onOpenOpportunity, onOpenMail],
  );

  const replan = useMutation({
    mutationFn: replanActions,
    onSuccess: () => {
      pushToast("Rebuilding your plan — this takes a moment.", "info");
      setTimeout(() => qc.invalidateQueries({ queryKey: ["actions"] }), 4000);
    },
    onError: () => pushToast("Couldn't rebuild the plan."),
  });

  // isPending, never isFetching: a background refresh must not blank the page.
  if (planQ.isPending) return <div className="today-empty">Working out your day…</div>;
  if (!plan) return <div className="today-empty">Couldn&apos;t load your plan.</div>;

  const nothingLeft = plan.overdue.length === 0 && plan.today.length === 0;

  return (
    <div className="today">
      <div className="today-head">
        <div>
          <h1>Today</h1>
          <div className="today-sub">{summary(plan)}</div>
        </div>
        <div className="today-actions">
          <button
            className="today-replan"
            onClick={() => replan.mutate()}
            disabled={replan.isPending}
          >
            {replan.isPending ? "Rebuilding…" : "Rebuild plan"}
          </button>
        </div>
      </div>

      {nothingLeft ? (
        // The empty state IS the reward. A to-do list that can be finished is the entire
        // difference between this and a dashboard, so it has to read like an ending.
        <div className="today-done">
          <div className="today-done-mark">✓</div>
          <h2>Nothing left for today.</h2>
          <p>
            {plan.counts.upcoming > 0
              ? `${plan.counts.upcoming} task${plan.counts.upcoming === 1 ? "" : "s"} coming up later this week.`
              : "Nothing scheduled for the rest of the week either."}
          </p>
        </div>
      ) : (
        <>
          {plan.overdue.length > 0 && (
            <Section title="Overdue" count={plan.overdue.length} tone="overdue">
              {plan.overdue.map((a) => (
                <ActionCard key={a.id} action={a} handlers={handlers} busy={busyId === a.id} />
              ))}
            </Section>
          )}
          {plan.today.length > 0 && (
            <Section title="Today" count={plan.today.length}>
              {plan.today.map((a) => (
                <ActionCard key={a.id} action={a} handlers={handlers} busy={busyId === a.id} />
              ))}
            </Section>
          )}
        </>
      )}

      {plan.upcoming.length > 0 && (
        <div className="today-upcoming">
          <span className="today-upcoming-label">Coming up</span>
          {plan.upcoming.map((u) => (
            <span key={u.day} className="today-upcoming-day">
              <b>{dayName(u.day)}</b> {u.count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  count,
  tone,
  children,
}: {
  title: string;
  count: number;
  tone?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`today-section ${tone ?? ""}`}>
      <h2>
        {title} <span className="today-count">{count}</span>
      </h2>
      <ul className="today-list">{children}</ul>
    </section>
  );
}

function summary(plan: TodayPlan): string {
  const bits: string[] = [];
  if (plan.counts.overdue) bits.push(`${plan.counts.overdue} overdue`);
  if (plan.counts.today) bits.push(`${plan.counts.today} for today`);
  if (!bits.length) return "You're clear.";
  const critical = plan.counts.critical
    ? ` · ${plan.counts.critical} can't wait`
    : "";
  return bits.join(" · ") + critical;
}

/** "Tue 12" — the coming-up strip is a shape, not a schedule, so short labels are enough. */
function dayName(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { weekday: "short", day: "numeric" });
}
