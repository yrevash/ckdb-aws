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

  it("derives rowsLost from missing + content-mismatched lists, not a reassuring 0", () => {
    // measured_value absent → must count the two lists, not fall through to 0.
    const view = resilienceViewFromReport({
      ...PHASE_THREE_RESILIENCE_REPORT,
      probes: {
        ...PHASE_THREE_RESILIENCE_REPORT.probes,
        rpo: {
          probe_type: "rpo",
          status: "fail",
          measured_value: null,
          unit: "rows_lost",
          details: {
            rows_expected: 8,
            rows_found: 5,
            rows_missing: ["evt-a", "evt-b"],
            rows_content_mismatched: ["evt-c"],
          },
        },
      },
    });
    expect(view?.rpo.rowsLost).toBe(3);
    // The old `num([...]) ?? 0` bug would have reported 0 here — hiding loss.
    expect(view?.rpo.rowsLost).not.toBe(0);
  });

  it("reports null rowsLost (not 0) when the report is malformed", () => {
    const view = resilienceViewFromReport({
      ...PHASE_THREE_RESILIENCE_REPORT,
      probes: {
        ...PHASE_THREE_RESILIENCE_REPORT.probes,
        rpo: {
          probe_type: "rpo",
          status: "fail",
          measured_value: null,
          unit: "rows_lost",
          details: { rows_expected: 8, rows_found: 8 },
        },
      },
    });
    expect(view?.rpo.rowsLost).toBeNull();
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
