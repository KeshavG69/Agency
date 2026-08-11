"use client";

// The Agent tab: what the agents did to one record, and whether anything is still running.
// Plan §7.1 and §7.4.
//
// REQUIRES <QueryProvider> above it in the tree (components/QueryProvider.tsx) — this is
// the first useQuery call in the app, which is the moment that file says to mount it.
//
// Two rules this component exists to honour:
//   * The poll STOPS. It runs at 3s only while work is outstanding, and terminates itself
//     otherwise — including behind a crashed worker, via the staleness cutoff.
//   * An error NEVER wipes the transcript. A fault renders beside the events we already
//     have, not instead of them.

import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  WORKING_POLL_MS,
  absoluteTime,
  classifyFault,
  deriveTrailState,
  describeEvent,
  relativeTime,
  shouldPoll,
  type AgentEvent,
  type TrailState,
} from "@/lib/agent-trail";
import { cn } from "@/lib/cn";
import { agentEventsQuery, queueHealthQuery } from "@/lib/queries";

// Stable identity: a fresh [] each render would make the activity check below fire on
// renders where nothing actually changed.
const NO_EVENTS: AgentEvent[] = [];

const STATE_LABEL: Record<TrailState, string> = {
  loading: "Loading",
  empty: "Idle",
  working: "Working",
  ended: "Up to date",
  offline: "Offline",
  failed: "Unavailable",
};

const STATE_TONE: Record<TrailState, string> = {
  loading: "text-muted-foreground",
  empty: "text-muted-foreground",
  working: "text-bid-ink",
  ended: "text-muted-foreground",
  // Losing the connection is a warning; the endpoint refusing is an error. Only the second
  // one is somebody's fault.
  offline: "text-watch",
  failed: "text-destructive",
};

export interface AgentTrailProps {
  /** An opportunity id, a contact email — whatever the agents recorded against. */
  subjectId: string;
  title?: string;
  limit?: number;
  className?: string;
}

export function AgentTrail({
  subjectId,
  title = "Agent trail",
  limit = 200,
  className,
}: AgentTrailProps) {
  // The stop condition is derived from BOTH queries, so it cannot be computed before they
  // run. Holding it in state instead of a ref is what makes the poll start at all: the
  // interval is read when react-query re-applies its options during render, and a ref
  // written further down that render would not be seen until some later, unrelated one.
  const [polling, setPolling] = useState(false);

  // Both queries share the interval, so the trail and the queue depth can never disagree
  // about whether this record is still moving. (Plan §7.1 writes this as a callback on
  // `q.state.data.working`; our `working` is a join across two endpoints plus a clock, so
  // it is derived once below and applied to both.)
  const trail = useQuery({
    ...agentEventsQuery(subjectId, limit),
    refetchInterval: polling ? WORKING_POLL_MS : false,
    // Nothing changes on focus that the poll has not already caught, and a focus refetch
    // on a finished record is exactly the wasted request this phase set out to remove.
    refetchOnWindowFocus: false,
  });

  const health = useQuery({
    ...queueHealthQuery(),
    refetchInterval: polling ? WORKING_POLL_MS : false,
  });

  const events = trail.data?.events ?? NO_EVENTS;

  // WHEN THE TRAIL LAST MOVED, on the browser's clock.
  //
  // Measured as "the moment we first saw this many events" rather than parsed out of the
  // newest `created_at`: the API host and this browser do not share a clock, and a couple
  // of minutes of skew would mark a just-written event as 90 seconds stale on arrival.
  // agent_events is append-only, so a change in length IS new activity.
  const activity = useRef({ subject: "", count: -1, at: 0 });
  if (
    trail.dataUpdatedAt > 0 &&
    (activity.current.subject !== subjectId || activity.current.count !== events.length)
  ) {
    // Written during render on purpose: it is a derivation of the response just received,
    // and re-running it (StrictMode, the state adjustment below) yields the same values.
    activity.current = { subject: subjectId, count: events.length, at: trail.dataUpdatedAt };
  }

  // The last time we heard anything at all from the server — same clock as `activity.at`.
  const now = Math.max(trail.dataUpdatedAt, health.dataUpdatedAt);

  // A failing /tasks/health is tolerated rather than surfaced: it only decides whether to
  // keep polling, so losing it degrades to "assume nothing is queued", which ends the poll
  // — the safe direction. Only the trail's own failure is a fault worth showing.
  const fault = trail.error ? classifyFault(trail.error) : null;
  const openTasks = health.data?.open ?? 0;

  const state = deriveTrailState({
    loaded: trail.data !== undefined,
    eventCount: events.length,
    openTasks,
    lastActivityAt: activity.current.at,
    now,
    fault,
  });

  // Adjusting state during render (the supported pattern, not an effect): React re-runs
  // this component immediately with the new value, so react-query picks the interval up in
  // the same commit rather than a tick late.
  const working = shouldPoll(state);
  if (polling !== working) setPolling(working);

  const recheck = () => {
    void trail.refetch();
    void health.refetch();
  };

  const hasTranscript = events.length > 0;
  const busy = trail.isFetching || health.isFetching;

  return (
    <section
      className={cn("overflow-hidden rounded-lg border bg-card", className)}
      aria-busy={state === "working"}
    >
      <header className="flex items-center justify-between gap-3 border-b px-4 py-2.5">
        <h2 className="text-sm font-medium">{title}</h2>

        <div className="flex items-center gap-2">
          {/* The only live region. The state changes rarely; the transcript changes often
              and would otherwise be read out in full on every poll. */}
          <span
            role="status"
            aria-live="polite"
            className={cn("flex items-center gap-1.5 text-xs", STATE_TONE[state])}
          >
            <span
              aria-hidden
              className={cn(
                "size-1.5 rounded-full bg-current",
                state === "working" && "animate-pulse"
              )}
            />
            {STATE_LABEL[state]}
            {state === "working" && openTasks > 0 ? ` · ${openTasks} queued` : ""}
          </span>

          {state !== "loading" && state !== "working" && (
            <button
              type="button"
              onClick={recheck}
              disabled={busy}
              className="rounded-sm border px-2 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            >
              {busy ? "Checking…" : "Check again"}
            </button>
          )}
        </div>
      </header>

      {fault && (
        <p
          role="alert"
          className={cn(
            "border-b px-4 py-2 text-xs",
            // --watch-soft is the measured 8% tint from legacy.css (AA against --watch);
            // the destructive pair has no such token, so the opacity modifier mixes it.
            fault === "offline"
              ? "bg-[var(--watch-soft)] text-watch"
              : "bg-destructive/5 text-destructive"
          )}
        >
          {fault === "offline"
            ? "Cannot reach the server."
            : "The agent trail could not be read."}{" "}
          {/* THE RULE: the transcript stays on screen. An outage is not a reason to make a
              rep forget what they were reading. */}
          {hasTranscript ? "Showing the last version loaded." : "Nothing to show yet."}
        </p>
      )}

      {hasTranscript ? (
        <ol className="ml-[1.375rem] border-l py-1.5 pr-3">
          {events.map((event, index) => {
            const step = describeEvent(event);
            return (
              <li key={`${event.created_at}-${index}`} className="relative py-2 pl-4">
                <span
                  aria-hidden
                  className={cn(
                    "absolute -left-[4px] top-3 size-[7px] rounded-full ring-2 ring-card",
                    step.tone === "warning" ? "bg-watch" : "bg-border"
                  )}
                />
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    {step.agentLabel}
                  </span>
                  <time
                    dateTime={step.at}
                    title={absoluteTime(step.at)}
                    className="text-[11px] text-muted-foreground"
                  >
                    {relativeTime(step.at, now)}
                  </time>
                  {step.tool && (
                    <span className="rounded-sm bg-muted px-1 font-mono text-[10px] text-muted-foreground">
                      {step.tool}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-sm leading-snug break-words">
                  {/* A refusal reads in the WARNING colour, never the destructive one — the
                      agent did its job, the answer was just no. */}
                  <span className={step.tone === "warning" ? "text-watch" : "text-foreground"}>
                    {step.headline}
                  </span>
                  {step.reason && <span className="text-muted-foreground"> — {step.reason}</span>}
                </p>
              </li>
            );
          })}
        </ol>
      ) : state === "loading" ? (
        // isPending may show a skeleton; isFetching may not (plan §5.2) — which is why this
        // branch becomes unreachable the moment a single event exists.
        <div className="space-y-3 px-4 py-4">
          {[0, 1, 2].map((row) => (
            <div key={row} className="animate-pulse space-y-1.5">
              <div className="h-2 w-24 rounded-sm bg-muted" />
              <div className="h-3 rounded-sm bg-muted" style={{ width: `${70 - row * 12}%` }} />
            </div>
          ))}
        </div>
      ) : (
        <div className="px-4 py-6 text-sm text-muted-foreground">
          {state === "working"
            ? "Work is queued for this record — nothing recorded yet."
            : "No agent has touched this record yet."}
        </div>
      )}

      {state === "ended" && openTasks > 0 && (
        // The staleness cutoff fired while the queue still reports work. Say so rather than
        // pretend to be idle — and leave "Check again" as the way back in.
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">
          Nothing new for a while, though {openTasks} background job
          {openTasks === 1 ? " is" : "s are"} still queued for your organisation.
        </p>
      )}
    </section>
  );
}

export default AgentTrail;
