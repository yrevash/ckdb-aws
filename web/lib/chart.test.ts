import { describe, expect, it } from "vitest";

import {
  clamp,
  clamp01,
  describeArc,
  linePath,
  linearScale,
  niceTicks,
  polarToCartesian,
  stepAreaPath,
  stepPath,
} from "@/lib/chart";

describe("chart geometry", () => {
  it("clamps", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(99, 0, 10)).toBe(10);
    expect(clamp01(1.5)).toBe(1);
  });

  it("maps a linear scale and guards a zero-width domain", () => {
    const s = linearScale(0, 10, 0, 100);
    expect(s(0)).toBe(0);
    expect(s(5)).toBe(50);
    expect(s(10)).toBe(100);
    // Degenerate domain must not divide by zero.
    const flat = linearScale(4, 4, 0, 100);
    expect(flat(4)).toBe(0);
  });

  it("produces nice ticks snapped to 1/2/5", () => {
    expect(niceTicks(0, 1, 4)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(0, 10, 5)).toEqual([0, 2, 4, 6, 8, 10]);
    // Degenerate range returns the single value.
    expect(niceTicks(3, 3)).toEqual([3]);
  });

  it("places polar points with 0 degrees at 12 o'clock", () => {
    const top = polarToCartesian(0, 0, 10, 0);
    expect(top.x).toBeCloseTo(0, 6);
    expect(top.y).toBeCloseTo(-10, 6);
    const right = polarToCartesian(0, 0, 10, 90);
    expect(right.x).toBeCloseTo(10, 6);
    expect(right.y).toBeCloseTo(0, 6);
  });

  it("describes an arc as an SVG path", () => {
    const d = describeArc(50, 50, 40, -90, 90);
    expect(d.startsWith("M ")).toBe(true);
    expect(d).toContain("A 40 40");
  });

  it("builds line, step and area paths", () => {
    const pts = [
      { x: 0, y: 10 },
      { x: 10, y: 4 },
      { x: 20, y: 10 },
    ];
    expect(linePath(pts)).toBe("M 0 10 L 10 4 L 20 10");
    // Step holds y then jumps: horizontal to next x at old y, then vertical.
    expect(stepPath(pts)).toBe("M 0 10 L 10 10 L 10 4 L 20 4 L 20 10");
    expect(linePath([])).toBe("");
    const area = stepAreaPath(pts, 100);
    expect(area.endsWith("Z")).toBe(true);
    expect(area).toContain("L 20 100");
  });
});
