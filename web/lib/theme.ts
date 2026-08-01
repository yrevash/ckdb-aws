/**
 * Shared theme constants for the chart kit. Charts reference design tokens by
 * CSS custom-property name (defined once in globals.css, themed light+dark) so
 * a colour changes in exactly one place. The categorical series order is the
 * dataviz reference palette, validated in both modes (worst adjacent CVD ΔE 9.1
 * light / 8.4 dark) — assign in fixed order, never cycled.
 */

export type ThemeName = "light" | "dark";

/** Categorical series slots, in fixed assignment order (dataviz validated). */
export const SERIES_VARS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
] as const;

/** Reserved semantic entity/status accents — always paired with icon + label. */
export const ACCENT_VARS = {
  brand: "var(--accent)",
  memory: "var(--memory)",
  action: "var(--action)",
  kill: "var(--kill)",
  good: "var(--status-good)",
  warn: "var(--status-warn)",
  critical: "var(--status-critical)",
} as const;

export type AccentRole = keyof typeof ACCENT_VARS;
