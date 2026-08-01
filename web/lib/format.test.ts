import { describe, expect, it } from "vitest";

import {
  ABSENT,
  formatCount,
  formatDuration,
  formatMs,
  formatPercent,
  formatRatio,
} from "@/lib/format";

describe("format", () => {
  it("rounds a ratio to two decimals", () => {
    expect(formatRatio(0.846154)).toBe("0.85");
    expect(formatRatio(0.94322)).toBe("0.94");
  });

  it("renders the em-dash for absent values, never a fake zero", () => {
    expect(formatRatio(null)).toBe(ABSENT);
    expect(formatRatio(undefined)).toBe(ABSENT);
    expect(formatCount(null)).toBe(ABSENT);
    expect(formatMs(undefined)).toBe(ABSENT);
    expect(formatDuration(null)).toEqual({ value: ABSENT, unit: "" });
    expect(formatRatio(Number.NaN)).toBe(ABSENT);
  });

  it("formats percents from 0..1", () => {
    expect(formatPercent(1)).toBe("100%");
    expect(formatPercent(0.943, 1)).toBe("94.3%");
  });

  it("formats counts with separators", () => {
    expect(formatCount(9)).toBe("9");
    expect(formatCount(1234)).toBe("1,234");
    expect(formatCount(0)).toBe("0");
  });

  it("splits duration into value + adaptive unit", () => {
    expect(formatDuration(0.099)).toEqual({ value: "99", unit: "ms" });
    expect(formatDuration(6.91)).toEqual({ value: "6.9", unit: "s" });
  });

  it("formats milliseconds", () => {
    expect(formatMs(7.418)).toBe("7.4 ms");
    expect(formatMs(204)).toBe("204 ms");
  });
});
