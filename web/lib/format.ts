/**
 * Display formatters for the console. Kept pure and unit-tested so every
 * on-screen number is produced one way. These format REAL values from the data
 * mappers; they never invent or default a value (Reality Charter R6) — an
 * `undefined`/`null` input formats to the em-dash, not a plausible-looking zero.
 */

/** The single "absent value" glyph used everywhere a number is unknown. */
export const ABSENT = "—";

/** A 0..1 ratio as a 2-decimal figure (0.846154 → "0.85"). */
export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return ABSENT;
  return value.toFixed(digits);
}

/** A 0..1 ratio as a whole percent (1.0 → "100%", 0.943 → "94%"). */
export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return ABSENT;
  return `${(value * 100).toFixed(digits)}%`;
}

/** A count with thousands separators (1234 → "1,234"). */
export function formatCount(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return ABSENT;
  return Math.round(value).toLocaleString("en-US");
}

/**
 * Seconds, adaptively: sub-second stays in ms ("99 ms"), otherwise seconds with
 * one decimal ("6.9 s"). Returns the raw split so a StatTile can render the unit
 * separately from the value.
 */
export function formatDuration(seconds: number | null | undefined): {
  value: string;
  unit: string;
} {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) {
    return { value: ABSENT, unit: "" };
  }
  if (seconds < 1) {
    return { value: Math.round(seconds * 1000).toString(), unit: "ms" };
  }
  return { value: seconds.toFixed(1), unit: "s" };
}

/** Milliseconds with one decimal below 100 ("7.4 ms"), whole above. */
export function formatMs(ms: number | null | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return ABSENT;
  return `${ms < 100 ? ms.toFixed(1) : Math.round(ms).toString()} ms`;
}
