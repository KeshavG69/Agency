"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import MailTriagePanel from "./MailTriage";
import { opportunityCountsQuery, queueHealthQuery } from "@/lib/queries";
import { dueLabel } from "@/lib/format";
import type { Opportunity } from "@/lib/data";

/**
 * Dashboard — the state of the operation.
 *
 * WHAT IT IS FOR, AND WHAT IT IS NOT. `Today` answers "what do I do"; this answers "where is
 * the work standing still". The two must not converge: the moment this page grows a task list
 * it becomes a worse copy of Today, which is exactly what it used to be — three renderings of
 * one fact (a ring, a month calendar and a grouped list, all of them Bid deadlines) stacked
 * into 3,400px of scroll.
 *
 * THE RULE THIS PAGE IS BUILT ON: never hide the bad number. The old version headlined "203
 * active pursuits" and then drew a ring reading 36, because the agenda silently dropped every
 * pursuit whose deadline had passed. 167 dead pursuits vanished between two adjacent numbers.
 * Everything here is a whole population, and the uncomfortable slice of it gets a row of its
 * own with a way to go fix it.
 *
 * It fits one screen deliberately. A management surface you have to scroll is a report.
 */

/** A bottleneck row: a population that is waiting on a human, and where to go act on it. */
type Blocker = {
  key: string;
  count: number;
  label: string;
  /** Why these are stuck — stated as fact, never as an instruction. */
  why: string;
  tone: "watch" | "nobid" | "bid";
  /** Pipeline status filter this row hands off to. */
  status: string;
};

export default function DashboardView({
  opps,
  loading,
  onOpen,
  onNavigatePipeline,
}: {
  /** The org's full Bid set (fetchBids pages internally, so this is all of them). */
  opps: Opportunity[];
  loading: boolean;
  onOpen: (id: string) => void;
  /** Jump to the Pipeline with a status filter applied. */
  onNavigatePipeline: (status: string) => void;
}) {
  // Unfiltered on purpose. `page.tsx` keeps its own counts keyed to the Pipeline's filters;
  // reusing those would make this page change meaning when someone filters a different view.
  const countsQ = useQuery(opportunityCountsQuery({}));
  const queueQ = useQuery(queueHealthQuery());
  const counts = countsQ.data?.counts ?? {};
  const queue = queueQ.data;

  const bids = useMemo(() => opps.filter((o) => o.bid_decision === "Bid"), [opps]);

  /**
   * The Bid set split by where each pursuit actually stands. Computed in one pass because
   * every figure on the left column is a slice of the same population and they must agree —
   * the previous page's contradiction came from two different denominators.
   */
  const bidState = useMemo(() => {
    let overdue = 0;
    let upcoming = 0;
    let undated = 0;
    let dueThisWeek = 0;
    let dueThisMonth = 0;
    let dueLater = 0;
    let captured = 0;
    let captureFailed = 0;

    for (const o of bids) {
      const { days } = dueLabel(o.response_deadline);
      if (days == null) undated++;
      else if (days < 0) overdue++;
      else {
        upcoming++;
        if (days <= 7) dueThisWeek++;
        else if (days <= 31) dueThisMonth++;
        else dueLater++;
      }
      if (o.captured_at) captured++;
      else if (o.capture_failed_at) captureFailed++;
    }

    return {
      overdue,
      upcoming,
      undated,
      dueThisWeek,
      dueThisMonth,
      dueLater,
      captured,
      captureFailed,
      // Live pursuits only — an expired one is not waiting on capture, it is waiting on a
      // decision to close it.
      captureNotStarted: bids.length - overdue - captured - captureFailed,
    };
  }, [bids]);

  /** The Analyst's whole output, as proportions of everything it has ever read. */
  const intake = useMemo(() => {
    const bid = counts.Bid ?? 0;
    const watch = counts.Watch ?? 0;
    const noBid = counts["No-Bid"] ?? 0;
    const screened = bid + watch + noBid;
    return { bid, watch, noBid, screened, all: counts.all ?? screened };
  }, [counts]);

  const blockers: Blocker[] = useMemo(() => {
    const rows: Blocker[] = [];
    if (intake.watch > 0)
      rows.push({
        key: "watch",
        count: intake.watch,
        label: "Waiting on a human decision",
        why: "The Analyst returned Watch. Nobody has ruled bid or no-bid.",
        tone: "watch",
        status: "Watch",
      });
    if (bidState.overdue > 0)
      rows.push({
        key: "overdue",
        count: bidState.overdue,
        label: "Past their response date",
        why: "Still marked Bid after the solicitation closed.",
        tone: "nobid",
        status: "Bid",
      });
    if (bidState.captureFailed > 0)
      rows.push({
        key: "capture-failed",
        count: bidState.captureFailed,
        label: "Capture run failed",
        why: "The capture agent stopped before producing documents.",
        tone: "nobid",
        status: "Bid",
      });
    if (bidState.captureNotStarted > 0)
      rows.push({
        key: "capture",
        count: bidState.captureNotStarted,
        label: "Capture not started",
        why: "Live pursuits with no capture documents produced yet.",
        tone: "bid",
        status: "Bid",
      });
    return rows;
  }, [intake.watch, bidState]);

  const opportunityTitles = useMemo(
    () => Object.fromEntries(opps.map((o) => [o.id, o.title])),
    [opps],
  );

  /** Soonest-first, upcoming only — the shortlist, not the agenda the old page scrolled. */
  const soonest = useMemo(
    () =>
      bids
        .map((o) => ({ o, due: dueLabel(o.response_deadline) }))
        .filter((r) => r.due.days != null && r.due.days >= 0)
        .sort((a, b) => (a.due.days ?? 0) - (b.due.days ?? 0))
        .slice(0, 5),
    [bids],
  );

  return (
    <div className="ops">
      <header className="ops-head">
        <h1>Dashboard</h1>
        <p className="ops-sub">
          Where the work is standing still. Your day&apos;s tasks are on <b>Today</b>.
        </p>
      </header>

      <section className="ops-intake" aria-labelledby="ops-intake-h">
        <div className="ops-intake-top">
          <h2 id="ops-intake-h">Analyst intake</h2>
          <p className="ops-intake-total">
            <b>{fmt(intake.screened)}</b> screened
            {intake.all > intake.screened && (
              <span className="ops-dim"> of {fmt(intake.all)} ingested</span>
            )}
          </p>
        </div>

        {intake.screened > 0 ? (
          <>
            <div
              className="ops-bar"
              role="img"
              aria-label={`${fmt(intake.noBid)} no-bid, ${fmt(intake.watch)} watch, ${fmt(intake.bid)} bid`}
            >
              <span
                className="ops-bar-seg nobid"
                style={{ flexGrow: intake.noBid || 0.0001 }}
              />
              <span
                className="ops-bar-seg watch"
                style={{ flexGrow: intake.watch || 0.0001 }}
              />
              <span className="ops-bar-seg bid" style={{ flexGrow: intake.bid || 0.0001 }} />
            </div>
            <div className="ops-legend">
              <LegendItem
                tone="nobid"
                label="Ruled out"
                count={intake.noBid}
                total={intake.screened}
                onClick={() => onNavigatePipeline("No-Bid")}
              />
              <LegendItem
                tone="watch"
                label="Undecided"
                count={intake.watch}
                total={intake.screened}
                onClick={() => onNavigatePipeline("Watch")}
              />
              <LegendItem
                tone="bid"
                label="Pursuing"
                count={intake.bid}
                total={intake.screened}
                onClick={() => onNavigatePipeline("Bid")}
              />
            </div>
          </>
        ) : (
          <p className="ops-empty">
            {countsQ.isPending
              ? "Counting what the Analyst has read…"
              : "The Analyst hasn't scored anything yet."}
          </p>
        )}
      </section>

      <div className="ops-grid">
        {/* Left: what a person owes. Right: what the clock and the machines are doing. The
            Agents panel sits under the blockers rather than beside them so the two columns
            end at roughly the same depth — the old page's signature flaw was a left column
            that stopped 2,000px above the right one. */}
        <div className="ops-side">
          <section className="ops-panel ops-blockers" aria-labelledby="ops-blockers-h">
            <h2 id="ops-blockers-h">Where it&apos;s stuck</h2>
            {loading && bids.length === 0 ? (
              <p className="ops-empty">Reading your pursuits…</p>
            ) : blockers.length === 0 ? (
              <p className="ops-empty">
                Nothing is waiting on a person. Every pursuit has been decided and every live one
                has capture under way.
              </p>
            ) : (
              <ul className="ops-blocker-list">
                {blockers.map((b) => (
                  <li key={b.key}>
                    <button
                      className="ops-blocker"
                      onClick={() => onNavigatePipeline(b.status)}
                      aria-label={`${b.count} ${b.label} — open in Pipeline`}
                    >
                      <span className={`ops-blocker-n ${b.tone}`}>{fmt(b.count)}</span>
                      <span className="ops-blocker-txt">
                        <span className="ops-blocker-label">{b.label}</span>
                        <span className="ops-blocker-why">{b.why}</span>
                      </span>
                      <svg
                        className="ops-blocker-go"
                        viewBox="0 0 16 16"
                        width="14"
                        height="14"
                        aria-hidden="true"
                      >
                        <path
                          d="M6 3.5 10.5 8 6 12.5"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.6"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="ops-panel" aria-labelledby="ops-agents-h">
            <h2 id="ops-agents-h">Agents</h2>
            <dl className="ops-rows">
              <Row label="Jobs queued" value={queue?.open ?? 0} />
              <Row
                label="Failed"
                value={queue?.gave_up ?? 0}
                tone={queue && queue.gave_up > 0 ? "nobid" : undefined}
              />
              <Row label="Capture complete" value={bidState.captured} tone="bid" />
            </dl>
            <p className="ops-note">
              {queueQ.isPending
                ? "Checking the queue…"
                : queue?.next_due
                  ? `Next run ${nextRun(queue.next_due)}.`
                  : "No runs scheduled."}
            </p>
          </section>
        </div>

        <div className="ops-side">
          <section className="ops-panel" aria-labelledby="ops-dead-h">
            <h2 id="ops-dead-h">Deadline pressure</h2>
            <dl className="ops-rows">
              <Row
                label="Due this week"
                value={bidState.dueThisWeek}
                tone={bidState.dueThisWeek > 0 ? "watch" : undefined}
              />
              <Row label="Later this month" value={bidState.dueThisMonth} />
              <Row label="Beyond a month" value={bidState.dueLater} />
              <Row label="No date set" value={bidState.undated} muted />
            </dl>
            {soonest.length > 0 && (
              <ul className="ops-soon">
                {soonest.map(({ o, due }) => (
                  <li key={o.id}>
                    <button className="ops-soon-row" onClick={() => onOpen(o.id)}>
                      <span className="ops-soon-title">{o.title}</span>
                      <span className={`due-chip ${due.tone}`}>{due.text}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      <MailTriagePanel opportunityTitles={opportunityTitles} onOpenOpportunity={onOpen} />
    </div>
  );
}

function LegendItem({
  tone,
  label,
  count,
  total,
  onClick,
}: {
  tone: string;
  label: string;
  count: number;
  total: number;
  onClick: () => void;
}) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <button className="ops-legend-item" onClick={onClick}>
      <span className={`ops-swatch ${tone}`} aria-hidden="true" />
      <span className="ops-legend-label">{label}</span>
      <span className="ops-legend-n">{fmt(count)}</span>
      <span className="ops-legend-pct">{pct}%</span>
    </button>
  );
}

function Row({
  label,
  value,
  tone,
  muted,
}: {
  label: string;
  value: number;
  tone?: string;
  muted?: boolean;
}) {
  return (
    <div className={`ops-row ${muted ? "muted" : ""}`}>
      <dt>{label}</dt>
      <dd className={tone ?? ""}>{fmt(value)}</dd>
    </div>
  );
}

/** Thousands separators — 1552 and 1,552 read very differently in a column of figures. */
function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

/** "at 11:00" today, otherwise "Tue 11:00". The queue only ever schedules hours ahead. */
function nextRun(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "soon";
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? `at ${time}` : `${d.toLocaleDateString("en-US", { weekday: "short" })} ${time}`;
}
