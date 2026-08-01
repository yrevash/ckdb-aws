"use client";

import type { ReactNode } from "react";

import { useAnimatedNumber } from "@/hooks/use-animated-number";
import { ABSENT } from "@/lib/format";
import type { AccentRole } from "@/lib/theme";
import { ACCENT_VARS } from "@/lib/theme";
import { SourceTag, type SourceKind } from "@/components/ui/source-tag";

export type StatState = "measured" | "pending" | "absent";

/**
 * The core artifact of the design language: one honest number with its unit and
 * provenance. Three states drive the whole visual grammar:
 *   · measured — a solid tile, a mono value counting in, a provenance tag;
 *   · pending  — a dashed tile, a ghosted "—", a "pending real run" tag (never a
 *                fabricated value — Reality Charter R7);
 *   · absent   — data not present; renders "—" (R6).
 *
 * Pass `value` as the already-formatted string. When `animateFrom` is a finite
 * number the tile counts up to it and renders via `format(n)`, so the animation
 * and the printed value cannot disagree.
 */
export function StatTile({
  label,
  value,
  unit,
  state = "measured",
  hint,
  accent = "brand",
  source,
  sourceDetail,
  animateFrom,
  format,
  aside,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  state?: StatState;
  hint?: ReactNode;
  accent?: AccentRole;
  source?: SourceKind;
  sourceDetail?: string;
  /** finite number → count up to it, printing with `format`. */
  animateFrom?: number;
  format?: (n: number) => string;
  aside?: ReactNode;
}) {
  const isPending = state !== "measured";
  const stateClass = state === "pending" ? " ui-stat--pending" : "";
  const accentVar = ACCENT_VARS[accent];

  return (
    <div
      className={`ui-stat${stateClass}`}
      style={{ ["--stat-accent" as string]: accentVar }}
    >
      <div className="ui-stat__top">
        <span className="ui-stat__label">{label}</span>
        {aside}
      </div>

      {isPending ? (
        <div className="ui-stat__value">
          <span
            className="ui-stat__num"
            aria-label={state === "pending" ? "not yet measured" : "no data"}
          >
            {ABSENT}
          </span>
        </div>
      ) : (
        <AnimatedValue
          value={value}
          unit={unit}
          animateFrom={animateFrom}
          format={format}
        />
      )}

      {hint ? <p className="ui-stat__hint">{hint}</p> : <span className="ui-stat__hint" />}

      <div className="ui-stat__foot">
        {source ? <SourceTag kind={source} detail={sourceDetail} /> : <span />}
      </div>
    </div>
  );
}

function AnimatedValue({
  value,
  unit,
  animateFrom,
  format,
}: {
  value: ReactNode;
  unit?: ReactNode;
  animateFrom?: number;
  format?: (n: number) => string;
}) {
  const shouldAnimate = typeof animateFrom === "number" && Number.isFinite(animateFrom) && !!format;
  const animated = useAnimatedNumber(shouldAnimate ? (animateFrom as number) : Number.NaN);
  const shown = shouldAnimate && format ? format(animated) : value;
  return (
    <div className="ui-stat__value">
      <span className="ui-stat__num">{shown}</span>
      {unit ? <span className="ui-stat__unit">{unit}</span> : null}
    </div>
  );
}
