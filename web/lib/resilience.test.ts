import { describe, expect, it } from "vitest";

import {
  PHASE_THREE_RESILIENCE_REPORT,
  failoverPhases,
  resilienceViewFromReport,
} from "@/lib/resilience";

describe("resilience report transport", () => {
  it("maps the postmortem-resilience-v1 report into a typed view", () => {
    const view = resilienceViewFromReport(PHASE_THREE_RESILIENCE_REPORT);
    expect(view).not.toBeNull();
    expect(view?.killedRegion).toBe("us-east-1");
    expect(view?.rpo.rowsLost).toBe(0);
    expect(view?.rpo.status).toBe("pass");
    expect(view?.rto.seconds).toBe(0.099);
    expect(view?.freshness.foundImmediately).toBe(true);
    expect(view?.crossAgent.crossRegion).toBe(true);
    expect(view?.atomicity.commitPass).toBe(true);
    expect(view?.atomicity.abortPass).toBe(true);
    expect(view?.liveness.beforeKill).toBe(9);
    expect(view?.liveness.duringOutage).toBe(6);
    expect(view?.liveness.afterRecovery).toBe(9);
    expect(view?.overall.pass).toBe(true);
  });

  it("rejects malformed reports", () => {
    expect(resilienceViewFromReport({ generated_at: "now" })).toBeNull();
    expect(resilienceViewFromReport(null)).toBeNull();
    expect(
      resilienceViewFromReport({
        ...PHASE_THREE_RESILIENCE_REPORT,
        probes: { rpo: { probe_type: "rpo" } },
      }),
    ).toBeNull();
  });

  it("holds RPO=0 across every failover phase (the money shot invariant)", () => {
    const view = resilienceViewFromReport(PHASE_THREE_RESILIENCE_REPORT)!;
    const phases = failoverPhases(view);

    expect(phases.map((phase) => phase.id)).toEqual([
      "steady",
      "region-down",
      "recovered",
    ]);
    // RPO must be pinned to the same value through the kill and back.
    expect(new Set(phases.map((phase) => phase.rpoRowsLost))).toEqual(new Set([0]));
    // Node liveness dips only while the region is down.
    expect(phases.map((phase) => phase.liveNodes)).toEqual([9, 6, 9]);
    expect(phases.find((phase) => phase.id === "region-down")?.regionDown).toBe(true);
  });
});
