/**
 * Bespoke SVG chart geometry — pure functions, no dependency, unit-tested.
 *
 * The chart kit is hand-built SVG (crisp axes, direct labels, no chartjunk per
 * the dataviz skill). All the math that decides where a mark lands lives here so
 * it can be verified in CI rather than eyeballed in a browser.
 */

export type Point = { x: number; y: number };

/** Clamp a number into [min, max]. */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Clamp into [0, 1]. */
export function clamp01(value: number): number {
  return clamp(value, 0, 1);
}

/**
 * A linear scale mapping a data domain onto a pixel range. Guards a zero-width
 * domain (returns the range start) so a single-value dataset never divides by 0.
 */
export function linearScale(
  domainMin: number,
  domainMax: number,
  rangeMin: number,
  rangeMax: number,
): (value: number) => number {
  const span = domainMax - domainMin;
  if (span === 0) return () => rangeMin;
  const k = (rangeMax - rangeMin) / span;
  return (value: number) => rangeMin + (value - domainMin) * k;
}

/**
 * "Nice" evenly-spaced ticks across [min, max] with roughly `count` steps,
 * snapped to 1/2/5·10ⁿ increments. Returns inclusive endpoints.
 */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return [min];
  const span = max - min;
  const rawStep = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  // Round each tick to the step's own precision so floating-point dust
  // (0.6000000000000001 → 0.6) never reaches an axis label.
  const decimals = Math.max(0, -Math.floor(Math.log10(step))) + 1;
  const start = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let i = 0; start + i * step <= max + step * 1e-9; i += 1) {
    ticks.push(Number((start + i * step).toFixed(decimals)));
  }
  return ticks;
}

/** Polar → cartesian, angle in degrees, 0° at 12 o'clock, clockwise. */
export function polarToCartesian(
  cx: number,
  cy: number,
  radius: number,
  angleDeg: number,
): Point {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + radius * Math.cos(rad), y: cy + radius * Math.sin(rad) };
}

/**
 * SVG arc path from `startAngle` to `endAngle` (degrees, clockwise) along a
 * circle of `radius`. Used for the gauge track and value arc.
 */
export function describeArc(
  cx: number,
  cy: number,
  radius: number,
  startAngle: number,
  endAngle: number,
): string {
  const start = polarToCartesian(cx, cy, radius, endAngle);
  const end = polarToCartesian(cx, cy, radius, startAngle);
  const largeArc = Math.abs(endAngle - startAngle) <= 180 ? 0 : 1;
  const sweep = endAngle >= startAngle ? 0 : 1;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} ${sweep} ${end.x} ${end.y}`;
}

/** A polyline `d` through the given points (line chart). */
export function linePath(points: Point[]): string {
  if (points.length === 0) return "";
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`)
    .join(" ");
}

/**
 * A step ("stairs") path through the points — value holds, then jumps — the
 * right shape for node-liveness that steps 9→6→9 across the kill.
 */
export function stepPath(points: Point[]): string {
  if (points.length === 0) return "";
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i += 1) {
    d += ` L ${points[i].x} ${points[i - 1].y} L ${points[i].x} ${points[i].y}`;
  }
  return d;
}

/** Close a step line into a filled area down to `baselineY`. */
export function stepAreaPath(points: Point[], baselineY: number): string {
  if (points.length === 0) return "";
  const line = stepPath(points);
  const last = points[points.length - 1];
  const first = points[0];
  return `${line} L ${last.x} ${baselineY} L ${first.x} ${baselineY} Z`;
}
