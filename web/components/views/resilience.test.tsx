import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { PHASE_THREE_RESILIENCE_REPORT } from "@/lib/resilience";
import {
  leaseholderSnapshotFromReport,
  regionColors,
} from "@/components/charts/resilience-leaseholders";
import { Resilience } from "@/components/views/resilience";

afterEach(cleanup);

describe("leaseholderSnapshotFromReport", () => {
  it("parses the real range_snapshot into the before → after leaseholder migration", () => {
    const snapshot = leaseholderSnapshotFromReport(PHASE_THREE_RESILIENCE_REPORT);
    expect(snapshot).not.toBeNull();
    expect(snapshot?.killedRegion).toBe("us-east-2");
    expect(snapshot?.regions).toEqual(["us-east-1", "us-east-2", "us-west-2"]);

    const before = snapshot?.phases.find((p) => p.id === "before");
    const after = snapshot?.phases.find((p) => p.id === "after");
    // Leaseholders start pinned to the (soon-to-be-killed) region…
    expect(before?.counts.find((c) => c.region === "us-east-2")?.leaseholders).toBe(9);
    expect(before?.counts.find((c) => c.region === "us-east-1")?.leaseholders).toBe(0);
    // …then migrate onto the survivors after the kill.
    expect(after?.counts.find((c) => c.region === "us-east-1")?.leaseholders).toBe(7);
    expect(after?.counts.find((c) => c.region === "us-west-2")?.leaseholders).toBe(2);
    expect(after?.counts.find((c) => c.region === "us-east-2")?.leaseholders).toBe(0);
  });

  it("returns null (→ fixture fallback) when the snapshot or topology is absent", () => {
    expect(leaseholderSnapshotFromReport(null)).toBeNull();
    expect(leaseholderSnapshotFromReport({ topology: { regions: ["a"] } })).toBeNull();
    expect(leaseholderSnapshotFromReport({ range_snapshot: {} })).toBeNull();
  });

  it("paints the killed region coral and survivors with validated series slots", () => {
    const colors = regionColors(["us-east-1", "us-east-2", "us-west-2"], "us-east-2");
    expect(colors["us-east-2"]).toBe("var(--kill)");
    expect(colors["us-east-1"]).toBe("var(--series-1)");
    expect(colors["us-west-2"]).not.toBe(colors["us-east-1"]);
    expect(colors["us-west-2"]).not.toBe("var(--kill)");
  });
});

describe("Resilience view", () => {
  it("renders the real region-kill proof from the replay fixture", () => {
    const { container } = render(<Resilience />);
    const text = container.textContent ?? "";

    expect(text).toContain("Survives");
    // Killed region + real RTO (3.874s → "3.9 s") + zero-loss story.
    expect(text).toContain("us-east-2");
    expect(text).toContain("3.9");
    expect(text).toContain("Recovery, verified during the outage");
    // The three headline probes and the two topology cards are present.
    expect(text).toContain("RTO");
    expect(text).toContain("RPO");
    expect(text).toContain("Read-your-writes");
    expect(text).toContain("Region topology");
    expect(text).toContain("Leaseholder distribution");
    // No stray em-dash where a measured figure should be.
    expect(text).not.toContain("99 ms");
  });
});
