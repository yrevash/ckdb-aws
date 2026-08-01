"use client";

import { useEffect, useRef, useState } from "react";

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Count a number up to `target` on mount — the one purposeful motion the brief
 * asks for (a value animating in), not ambient noise.
 *
 * `shouldAnimate` depends only on the target being finite, so the first render
 * is identical on server and client (no hydration mismatch). The reduced-motion
 * preference is read inside the effect, where it collapses the duration to 0 so
 * the value snaps to target on the first frame. The only setState is inside the
 * rAF callback — never synchronously in the effect body. A non-finite target
 * (an absent value) is returned untouched, never animated toward a fake 0.
 */
export function useAnimatedNumber(target: number, durationMs = 620): number {
  const shouldAnimate = Number.isFinite(target) && durationMs > 0;
  const [value, setValue] = useState(shouldAnimate ? 0 : target);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (!shouldAnimate) return;
    const dur = prefersReducedMotion() ? 0 : durationMs;

    let start: number | null = null;
    const tick = (now: number) => {
      if (start === null) start = now;
      const t = dur <= 0 ? 1 : Math.min(1, (now - start) / dur);
      // easeOutCubic — a readout that settles rather than snaps.
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(target * eased);
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);

    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [target, durationMs, shouldAnimate]);

  return shouldAnimate ? value : target;
}
