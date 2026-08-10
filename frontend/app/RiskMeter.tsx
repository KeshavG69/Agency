"use client";

import type { RiskFactor, RiskFactorKind, RiskLevel, RiskSeverity } from "@/lib/data";

/**
 * The Analyst's risk assessment: a meter for the headline, then every factor with the
 * reasoning behind it.
 *
 * The agent already separated HARD DISQUALIFIERS from CONFIRMABLE GATES in its reasoning —
 * it just used to bury both in a paragraph of rationale, where nothing could show or sort
 * them. This renders that judgement: how risky, and specifically why.
 *
 * A `blocker` is deliberately styled apart from the rest. "We are not eligible for this
 * set-aside" and "we should check the incumbent" are not the same kind of finding, and a rep
 * skimming a list must be able to tell them apart at a glance.
 */

const LEVEL_FILL: Record<RiskLevel, number> = { Low: 1, Medium: 2, High: 3 };

/** Human labels — the stored values are snake_case enum keys. */
const FACTOR_LABEL: Record<RiskFactorKind, string> = {
  capability: "Capability",
  eligibility: "Eligibility",
  competition: "Competition",
  past_performance: "Past performance",
  scope_clarity: "Scope clarity",
  schedule: "Schedule",
  contract_type: "Contract type",
  teaming: "Teaming",
};

const SEVERITY_LABEL: Record<RiskSeverity, string> = {
  blocker: "Blocker",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export default function RiskMeter({
  level,
  factors,
}: {
  level?: RiskLevel | null;
  factors?: RiskFactor[];
}) {
  // Nothing to say is better than an empty scaffold — opportunities analysed before this
  // existed simply have no risk data, and a blank meter would imply "no risk".
  if (!level && !(factors && factors.length)) return null;

  const lvl: RiskLevel = level ?? "Medium";
  const filled = LEVEL_FILL[lvl];
  // Blockers first, then by severity — the thing that kills the pursuit leads.
  const order: RiskSeverity[] = ["blocker", "high", "medium", "low"];
  const sorted = [...(factors ?? [])].sort(
    (a, b) => order.indexOf(a.severity) - order.indexOf(b.severity),
  );

  return (
    <div className="risk">
      <div className="risk-head">
        <span className="risk-label">Risk</span>
        <span className={`risk-bars r-${lvl.toLowerCase()}`} aria-hidden>
          {[1, 2, 3].map((i) => (
            <i key={i} className={i <= filled ? "on" : ""} />
          ))}
        </span>
        <span className={`risk-level r-${lvl.toLowerCase()}`}>{lvl}</span>
      </div>

      {sorted.length > 0 && (
        <ul className="risk-list">
          {sorted.map((f, i) => (
            <li key={`${f.factor}-${i}`} className={f.severity === "blocker" ? "blocker" : ""}>
              <span className={`risk-sev s-${f.severity}`}>{SEVERITY_LABEL[f.severity]}</span>
              <span className="risk-factor">{FACTOR_LABEL[f.factor] ?? f.factor}</span>
              <span className="risk-note">{f.note}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
