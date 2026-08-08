"use client";

import { MultiPicker } from "./FilterBar";

// Faceted filter over the call sheet. Client-side and instant — the whole call plan is
// already loaded — and deliberately built from the same pieces as the Pipeline's FilterBar
// (same MultiPicker, same .fb-* styling) so the two read as one product.
//
// The facets differ from the Pipeline's on purpose: everything here is already a live pursuit,
// so bid-decision / source / value would barely split the list. What a rep actually sorts a
// call sheet by is WHEN it's due, WHO is awarding, and finding one by name.
export type CallDue = "any" | "overdue" | "7" | "30" | "90";
export type CallStatus = "planned" | "done" | "dismissed" | "all";

export interface CallFilters {
  q: string;
  agencies: string[];
  due: CallDue;
  status: CallStatus;
}

export const EMPTY_CALL_FILTERS: CallFilters = {
  q: "",
  agencies: [],
  due: "any",
  status: "planned",
};

export function activeCallFilterCount(f: CallFilters): number {
  let n = 0;
  if (f.q.trim()) n++;
  if (f.agencies.length) n++;
  if (f.due !== "any") n++;
  if (f.status !== "planned") n++; // "planned" is the default view, not a filter
  return n;
}

const STATUSES: { key: CallStatus; label: string }[] = [
  { key: "planned", label: "To call" },
  { key: "done", label: "Done" },
  { key: "dismissed", label: "Dismissed" },
  { key: "all", label: "All" },
];

export default function CallPlanFilters({
  filters,
  onChange,
  agencyOptions,
  counts,
  onClear,
}: {
  filters: CallFilters;
  onChange: (f: CallFilters) => void;
  agencyOptions: string[];
  counts: Record<CallStatus, number>;
  onClear: () => void;
}) {
  const set = (patch: Partial<CallFilters>) => onChange({ ...filters, ...patch });
  const active = activeCallFilterCount(filters);

  return (
    <div className="filterbar">
      <div className="fb-decisions">
        {STATUSES.map((s) => (
          <button
            key={s.key}
            className={`fb-pill ${filters.status === s.key ? "on" : ""}`}
            onClick={() => set({ status: s.key })}
          >
            {s.label}
            <span className="fb-pill-ct">{counts[s.key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="fb-facets">
        <input
          className="fb-search cp-search"
          placeholder="Search pursuits, agencies, contacts…"
          value={filters.q}
          onChange={(e) => set({ q: e.target.value })}
        />
        <MultiPicker
          label="Agency"
          options={agencyOptions}
          selected={filters.agencies}
          onChange={(v) => set({ agencies: v })}
        />
        <select
          className="fb-select"
          value={filters.due}
          onChange={(e) => set({ due: e.target.value as CallDue })}
        >
          <option value="any">Any deadline</option>
          <option value="overdue">Overdue</option>
          <option value="7">Due ≤ 7 days</option>
          <option value="30">Due ≤ 30 days</option>
          <option value="90">Due ≤ 90 days</option>
        </select>
        {active > 0 && (
          <button className="fb-clearall" onClick={onClear}>
            Clear filters ({active})
          </button>
        )}
      </div>
    </div>
  );
}
