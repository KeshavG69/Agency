"use client";

import { useMemo } from "react";

/** YYYY-MM-DD in the *local* timezone (ISO-slicing a UTC date rolls the day for
 *  users west of UTC). */
export function toLocalIso(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const WEEKDAY = new Intl.DateTimeFormat("en-US", { weekday: "short" });

interface Props {
  /** YYYY-MM-DD currently focused, or null for "All". */
  selectedDate: string | null;
  /** YYYY-MM-DD values that have opportunities — drives the dot indicator. */
  availableDates: string[];
  /** How many past days (incl. today) to show. Defaults to 7. */
  windowDays?: number;
  /** Called with a YYYY-MM-DD (or null for "All") when the user picks. */
  onSelect: (date: string | null) => void;
}

/** A horizontal strip of recent days for browsing opportunities by posted date —
 *  modeled on PriceIQ's RFP Radar calendar. Each day shows a dot when it has
 *  opportunities; today is badged. */
export default function CalendarStrip({
  selectedDate,
  availableDates,
  windowDays = 7,
  onSelect,
}: Props) {
  const todayIso = useMemo(() => toLocalIso(new Date()), []);
  const available = useMemo(() => new Set(availableDates), [availableDates]);

  const days = useMemo(() => {
    const out: { iso: string; date: Date }[] = [];
    const today = new Date();
    for (let i = windowDays - 1; i >= 0; i--) {
      const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
      out.push({ iso: toLocalIso(d), date: d });
    }
    return out;
  }, [windowDays]);

  return (
    <div className="cal-strip">
      <button
        type="button"
        className={`cal-day cal-all ${selectedDate === null ? "on" : ""}`}
        onClick={() => onSelect(null)}
        aria-pressed={selectedDate === null}
      >
        <span className="cd-wd">All</span>
        <span className="cd-num">∞</span>
        <span className="cd-foot" />
      </button>
      {days.map(({ iso, date }) => {
        const isSel = iso === selectedDate;
        const isToday = iso === todayIso;
        const has = available.has(iso);
        return (
          <button
            key={iso}
            type="button"
            className={`cal-day ${isSel ? "on" : ""}`}
            onClick={() => onSelect(iso)}
            aria-pressed={isSel}
            aria-label={`Opportunities posted ${iso}${has ? " (available)" : ""}`}
          >
            <span className="cd-wd">{WEEKDAY.format(date)}</span>
            <span className="cd-num">{date.getDate()}</span>
            <span className="cd-foot">
              {isToday ? (
                <span className="cd-today">Today</span>
              ) : has ? (
                <span className="cd-dot" title="Opportunities available" />
              ) : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}
