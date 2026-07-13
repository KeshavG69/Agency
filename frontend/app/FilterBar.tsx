"use client";

import { useEffect, useMemo, useRef, useState } from "react";

// Per-user faceted filter over the pulled opportunities. Client-side (the list is
// already loaded) and instant. Selections are persisted per-user in localStorage.
export interface Facets {
  agencies: string[];
  naics: string[];
  setAsides: string[];
  source: "any" | "manual" | "sam.gov" | "excel";
  value: "any" | "lt1m" | "1to10m" | "gt10m";
  due: "any" | "7" | "30" | "90";
}

export const EMPTY_FACETS: Facets = {
  agencies: [],
  naics: [],
  setAsides: [],
  source: "any",
  value: "any",
  due: "any",
};

export function activeFacetCount(f: Facets): number {
  let n = 0;
  if (f.agencies.length) n++;
  if (f.naics.length) n++;
  if (f.setAsides.length) n++;
  if (f.source !== "any") n++;
  if (f.value !== "any") n++;
  if (f.due !== "any") n++;
  return n;
}

// A dropdown of checkboxes for a single multi-select facet (Agency / NAICS / …).
function MultiPicker({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? options.filter((o) => o.toLowerCase().includes(s)) : options;
  }, [options, q]);

  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);

  return (
    <div className="fb-picker" ref={ref}>
      <button
        className={`fb-btn ${selected.length ? "on" : ""}`}
        onClick={() => setOpen((o) => !o)}
        disabled={options.length === 0}
        title={options.length === 0 ? `No ${label.toLowerCase()} values yet` : undefined}
      >
        {label}
        {selected.length > 0 && <span className="fb-badge">{selected.length}</span>}
        <span className="fb-caret">▾</span>
      </button>
      {open && (
        <div className="fb-pop">
          {options.length > 8 && (
            <input
              className="fb-search"
              placeholder={`Search ${label.toLowerCase()}…`}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              autoFocus
            />
          )}
          <div className="fb-opts">
            {filtered.length === 0 ? (
              <div className="fb-none">No matches</div>
            ) : (
              filtered.map((o) => (
                <label key={o} className={`fb-opt ${selected.includes(o) ? "on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={selected.includes(o)}
                    onChange={() => toggle(o)}
                  />
                  <span className="fb-check" aria-hidden />
                  <span className="fb-optlabel">{o}</span>
                </label>
              ))
            )}
          </div>
          {selected.length > 0 && (
            <button className="fb-clearone" onClick={() => onChange([])}>
              Clear {label.toLowerCase()}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function FilterBar({
  filters,
  filter,
  onFilter,
  counts,
  facets,
  onFacets,
  options,
  onClear,
}: {
  filters: { key: string; label: string }[];
  filter: string;
  onFilter: (k: string) => void;
  counts: Record<string, number>;
  facets: Facets;
  onFacets: (f: Facets) => void;
  options: { agencies: string[]; naics: string[]; setAsides: string[] };
  onClear: () => void;
}) {
  const active = activeFacetCount(facets);
  const set = (patch: Partial<Facets>) => onFacets({ ...facets, ...patch });

  return (
    <div className="filterbar">
      <div className="fb-decisions">
        {filters.map((f) => (
          <button
            key={f.key}
            className={`fb-pill ${filter === f.key ? "on" : ""}`}
            onClick={() => onFilter(f.key)}
          >
            {f.label}
            <span className="fb-pill-ct">{counts[f.key] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="fb-facets">
        <MultiPicker
          label="Agency"
          options={options.agencies}
          selected={facets.agencies}
          onChange={(v) => set({ agencies: v })}
        />
        <MultiPicker
          label="NAICS"
          options={options.naics}
          selected={facets.naics}
          onChange={(v) => set({ naics: v })}
        />
        <MultiPicker
          label="Set-aside"
          options={options.setAsides}
          selected={facets.setAsides}
          onChange={(v) => set({ setAsides: v })}
        />
        <select
          className="fb-select"
          value={facets.source}
          onChange={(e) => set({ source: e.target.value as Facets["source"] })}
        >
          <option value="any">Any source</option>
          <option value="manual">Manual</option>
          <option value="sam.gov">SAM.gov</option>
          <option value="excel">Excel</option>
        </select>
        <select
          className="fb-select"
          value={facets.value}
          onChange={(e) => set({ value: e.target.value as Facets["value"] })}
        >
          <option value="any">Any value</option>
          <option value="lt1m">Under $1M</option>
          <option value="1to10m">$1M–$10M</option>
          <option value="gt10m">Over $10M</option>
        </select>
        <select
          className="fb-select"
          value={facets.due}
          onChange={(e) => set({ due: e.target.value as Facets["due"] })}
        >
          <option value="any">Any deadline</option>
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
