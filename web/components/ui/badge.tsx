import type { ReactNode } from "react";

export type Tone =
  | "neutral"
  | "good"
  | "warn"
  | "critical"
  | "memory"
  | "action"
  | "kill"
  | "accent";

/**
 * A compact pill for a single categorical fact (severity, region role, memory
 * kind). Semantic tones map to the reserved accents; never use a status tone
 * (good/warn/critical) for a plain category — those are reserved for state.
 */
export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: Tone;
  children: ReactNode;
}) {
  return <span className={`ui-badge ui-badge--${tone}`}>{children}</span>;
}

/** A small state dot. `pulse` marks a live/active state (respects reduced motion). */
export function StatusDot({
  tone = "neutral",
  pulse = false,
  label,
}: {
  tone?: "neutral" | "good" | "warn" | "critical" | "accent";
  pulse?: boolean;
  label?: string;
}) {
  return (
    <span
      className={`ui-dot ui-dot--${tone}${pulse ? " ui-dot--pulse" : ""}`}
      role={label ? "img" : undefined}
      aria-label={label}
    />
  );
}
