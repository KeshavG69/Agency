// The presentation layer for /api/intelligence — it turns the rows lib/intelligence.ts
// fetches into the words, the tone and the six states the Agent tab renders. Phase 7 of
// docs/frontend-implementation-plan.md.
//
// No HTTP and no React here on purpose: the poll's stop condition and the "is a refusal a
// warning?" decision are the two things in this feature most likely to be wrong, and both
// are pure functions you can reason about (and test) without mounting anything. The
// fetchers are re-exported at the bottom so a consumer has one import site.

import type { AgentEvent, ContactFact } from "@/lib/intelligence";

// --- the poll ------------------------------------------------------------------------

/** How often to re-read the trail WHILE work is outstanding. Never a background default. */
export const WORKING_POLL_MS = 3000;

/**
 * How long the trail may sit still before we stop calling it "working".
 *
 * Our agents are Celery tasks, not a stream, so "is something running?" is inferred from
 * `agent_tasks` depth — and a crashed worker leaves its row open forever. Without this
 * cutoff a dead queue and a busy queue look identical and the UI polls until the tab is
 * closed. 90s is long enough to sit through a slow LLM step and short enough that a human
 * has not yet gone to make tea.
 */
export const STALE_AFTER_MS = 90_000;

// --- who did it ----------------------------------------------------------------------

/** Agent ids as written by backend/tasks/*.py at their `record_event` call sites. */
export const AGENT_LABELS: Record<string, string> = {
  analyst: "Analyst",
  relation: "Relation",
  mail: "Mail",
  capture: "Capture",
  company_research: "Research",
};

/** Falls back to a prettified id so a NEW agent shows up readably with no frontend change. */
export function agentLabel(agent: string): string {
  const known = AGENT_LABELS[agent];
  if (known) return known;
  const words = (agent || "").replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Agent";
}

// --- what they did -------------------------------------------------------------------

interface StepRule {
  /** When set, the rule only applies to that agent — the same phrase can mean two things. */
  agent?: string;
  match: RegExp;
  say: (m: RegExpMatchArray) => string;
}

const count = (n: string, noun: string) =>
  `${n} ${noun}${Number(n) === 1 ? "" : "s"}`;

/**
 * (agent, step) -> the past-tense line a rep reads.
 *
 * This is an EXCEPTION LIST, not a translation table. The backend already writes `step` as
 * a short human phrase, so anything unmatched falls through verbatim — which is the point:
 * a new agent step needs no change here to display correctly. The rules exist for the two
 * shapes the raw string cannot carry:
 *
 *   1. Steps that spend an em-dash of their own ("No-Bid — priority 12"). The em-dash is
 *      reserved for the REASON (see `eventLine`), so the priority moves into parentheses
 *      rather than producing a line with two of them.
 *   2. Steps that ship a literal "(s)" from an f-string, which reads like a form.
 */
const VERBS: readonly StepRule[] = [
  {
    agent: "analyst",
    match: /^(Bid|No-Bid|Watch)\s*[—-]\s*priority\s*(-?\d+)/i,
    say: (m) => `judged it a ${m[1]} (priority ${m[2]})`,
  },
  {
    agent: "relation",
    match: /^surfaced\s+(\d+)\s+contact/i,
    say: (m) => `surfaced ${count(m[1], "contact")} from the network`,
  },
  {
    agent: "relation",
    match: /^no relevant contacts/i,
    say: () => "searched the network and found nobody worth engaging",
  },
  {
    agent: "mail",
    match: /^drafted\s+(\d+)\s+outreach email/i,
    say: (m) => `drafted ${count(m[1], "outreach email")}`,
  },
  {
    agent: "mail",
    match: /^re-?drafted/i,
    say: () => "re-drafted an outreach email",
  },
  {
    agent: "mail",
    match: /^no emailable contacts/i,
    say: () => "found no emailable contact to write to",
  },
  {
    agent: "capture",
    match: /^produced\s+(\d+)\s+deliverable/i,
    say: (m) => `produced ${count(m[1], "deliverable")}`,
  },
];

/**
 * A refusal is a WARNING, not an error.
 *
 * `ok: false` means an agent decided against something or came up empty — a No-Bid, a
 * contact search that found nobody, a company it could not identify. That is the system
 * working, and it is the half of the trail people actually come looking for. Painting it
 * destructive-red teaches reps to read "the agent broke" into "the agent said no".
 */
export type EventTone = "muted" | "warning";

export interface DescribedEvent {
  agent: string;
  agentLabel: string;
  /** Past-tense verb phrase — what happened. */
  headline: string;
  /** WHY it happened. Rendered after an em-dash; may be empty. */
  reason: string;
  tone: EventTone;
  tool: string | null;
  at: string;
}

export function describeEvent(event: AgentEvent): DescribedEvent {
  const step = (event.step || "").trim();
  const rule = VERBS.find((r) => (!r.agent || r.agent === event.agent) && r.match.test(step));
  const matched = rule ? step.match(rule.match) : null;
  const headline = rule && matched ? rule.say(matched) : step;

  const detail = (event.detail || "").trim();
  return {
    agent: event.agent,
    agentLabel: agentLabel(event.agent),
    headline: headline || "did something unrecorded",
    // Some call sites pass the same sentence twice; splicing it after an em-dash would
    // just say it again.
    reason: detail && detail !== headline && detail !== step ? detail : "",
    tone: event.ok ? "muted" : "warning",
    tool: event.tool ?? null,
    at: event.created_at,
  };
}

/** The whole line as one string — for aria-labels, titles and copy-to-clipboard. */
export function eventLine(described: DescribedEvent): string {
  return described.reason ? `${described.headline} — ${described.reason}` : described.headline;
}

// --- the six states ------------------------------------------------------------------

/** Not being able to reach the API is a different problem from the API saying no. */
export type TrailFault = "offline" | "failed";

export type TrailState = "loading" | "empty" | "working" | "ended" | "offline" | "failed";

export function classifyFault(error: unknown): TrailFault {
  // The browser knowing it is offline outranks anything the error object claims.
  if (typeof navigator !== "undefined" && navigator.onLine === false) return "offline";
  // An axios error carrying no `response` never reached the server at all: DNS, CORS, a
  // dead API host. That is connectivity, not a broken endpoint — and the difference is
  // the difference between "check your wifi" and "tell someone".
  if (isAxiosLikeError(error) && !error.response) return "offline";
  return "failed";
}

// Structural check rather than `axios.isAxiosError`, so this module stays importable from
// anywhere without pulling axios into the bundle for one boolean.
function isAxiosLikeError(error: unknown): error is { response?: unknown } {
  return typeof error === "object" && error !== null && "isAxiosError" in error;
}

export interface TrailStateInput {
  /** Has a first response landed? Distinguishes "loading" from "nothing ever happened". */
  loaded: boolean;
  eventCount: number;
  /** `open` from /tasks/health. Org-wide — the endpoint carries no subject. */
  openTasks: number;
  /** When the trail last MOVED, on the browser's clock. See `deriveTrailState`. */
  lastActivityAt: number;
  now: number;
  fault: TrailFault | null;
}

/**
 * The one place the six states are decided.
 *
 * Two things worth knowing about `lastActivityAt`:
 *
 *   * It must be measured on the BROWSER's clock — the moment we first saw the newest
 *     event — not parsed out of `created_at`. Server/browser clock skew of a couple of
 *     minutes would otherwise mark a just-written event as stale on arrival.
 *   * Before any event exists it is the first-response time, so a subject whose very first
 *     run is still starting up still gets its full working window.
 *
 * A fault wins the state but NEVER the render: the caller keeps showing the transcript it
 * already has and puts the fault beside it (plan §7.4).
 */
export function deriveTrailState(input: TrailStateInput): TrailState {
  const { loaded, eventCount, openTasks, lastActivityAt, now, fault } = input;
  if (fault) return fault;
  if (!loaded) return "loading";
  // Queue depth alone would spin forever behind a crashed worker; freshness alone would
  // call a quiet-but-queued record "done". Working means BOTH.
  if (openTasks > 0 && now - lastActivityAt < STALE_AFTER_MS) return "working";
  if (eventCount === 0) return "empty";
  return "ended";
}

/** The poll's stop condition, named. `refetchInterval` returns false on anything else. */
export function shouldPoll(state: TrailState): boolean {
  return state === "working";
}

// --- timestamps ----------------------------------------------------------------------

/**
 * Parse a timestamp off this API.
 *
 * The two stores disagree about timezones: `events_store` opens Mongo with `tz_aware=True`
 * so its `created_at` serialises with a `+00:00` offset, while `facts_store` does not, so
 * its dates arrive bare ("2026-08-05T12:00:00"). JS reads a bare date-time as LOCAL, which
 * silently shifts a fact's provenance by the viewer's offset — a European sees tomorrow's
 * date on this morning's evidence. Normalising here is cheaper than caring which store a
 * given string came from.
 */
export function parseServerTime(value?: string | null): Date | null {
  if (!value) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  const parsed = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** "just now" / "4m ago" / "3h ago" / "2d ago". `now` is injected so a render stays pure. */
export function relativeTime(value: string | null | undefined, now: number): string {
  const at = parseServerTime(value);
  if (!at) return "";
  const mins = Math.max(0, Math.round((now - at.getTime()) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Absolute, for the tooltip's bottom line and the `title` on a relative label. */
export function absoluteTime(value: string | null | undefined): string {
  const at = parseServerTime(value);
  if (!at) return "";
  return at.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** The host behind a piece of evidence — provenance a rep can judge at a glance. */
export function hostOf(url?: string | null): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** The first citable source behind a fact, for the tooltip's date+host line. */
export function primarySourceHost(fact: Pick<ContactFact, "evidence">): string {
  for (const item of fact.evidence ?? []) {
    const host = hostOf(item.source_url);
    if (host) return host;
  }
  return "";
}

// --- fetchers ------------------------------------------------------------------------
// Re-exported so the Agent tab imports from one module. The HTTP itself stays in
// lib/intelligence.ts, which is the only file that knows a URL.

export { fetchAgentEvents, fetchQueueHealth } from "@/lib/intelligence";
export type { AgentEvent, QueueHealth } from "@/lib/intelligence";
