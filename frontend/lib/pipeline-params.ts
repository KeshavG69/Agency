/**
 * The Pipeline's filter state, held in the URL.
 *
 * WHY THE URL AND NOT localStorage: these filters used to live in localStorage, which meant
 * a filtered view could not be shared, linked, or reached with the back button — "all SDVOSB
 * IT opportunities closing this month" was a thing you could look at but not send to anyone.
 * It also made the state invisible to anything server-side.
 *
 * ⚠️ `parseAsNativeArrayOf`, NOT `parseAsArrayOf`. The two differ in URL shape:
 *      parseAsArrayOf       -> ?agency=A,B          (one comma-joined value)
 *      parseAsNativeArrayOf -> ?agency=A&agency=B   (repeated keys)
 * The backend reads these as `agency: list[str] = Query(default=[])` (FastAPI), which requires
 * REPEATED KEYS. With the comma form it would receive the single string "A,B" and silently
 * match nothing. The keys below are deliberately the backend's own names for the same reason —
 * the query string can be forwarded as-is.
 */
import {
  parseAsNativeArrayOf,
  parseAsString,
  parseAsStringLiteral,
} from "nuqs";

export const SOURCES = ["any", "manual", "sam.gov", "excel"] as const;
export const VALUES = ["any", "lt1m", "1to10m", "gt10m"] as const;
export const DUES = ["any", "7", "30", "90"] as const;

/**
 * Everything except the search box. `q` is declared separately in the page because it needs a
 * debounce, and mixing a debounced key into a group would debounce the whole group.
 *
 * Every parser has a default, so each value is non-nullable and nuqs strips it from the URL
 * when it equals that default (clearOnDefault is on by default). An unfiltered pipeline
 * therefore has a clean `/` rather than a URL full of `?status=all&source=any`.
 */
export const pipelineParsers = {
  status: parseAsString.withDefault("all"),
  agency: parseAsNativeArrayOf(parseAsString).withDefault([]),
  naics: parseAsNativeArrayOf(parseAsString).withDefault([]),
  set_aside: parseAsNativeArrayOf(parseAsString).withDefault([]),
  source: parseAsStringLiteral(SOURCES).withDefault("any"),
  value: parseAsStringLiteral(VALUES).withDefault("any"),
  due: parseAsStringLiteral(DUES).withDefault("any"),
};

export type PipelineParams = {
  status: string;
  agency: string[];
  naics: string[];
  set_aside: string[];
  source: (typeof SOURCES)[number];
  value: (typeof VALUES)[number];
  due: (typeof DUES)[number];
};
