import { describe, expect, it } from "vitest";

import {
  DRIFT_MEMORY_CORPUS,
  PHASE_THREE_TEMPORAL_REPORT,
  summarizeActions,
  temporalDriftViewFromReport,
} from "@/lib/temporal-drift";

describe("temporal-drift transport", () => {
  it("joins the eval report and corpus into a valid-time transition view", () => {
    const view = temporalDriftViewFromReport(
      PHASE_THREE_TEMPORAL_REPORT,
      DRIFT_MEMORY_CORPUS,
    );
    expect(view).not.toBeNull();
    expect(view?.temporalValidityAccuracy).toBe(1);
    expect(view?.staleFactApplications).toBe(0);
    expect(view?.meetsTarget).toBe(true);
    expect(view?.families).toHaveLength(2);

    const pool = view?.families.find(
      (family) => family.familyId === "F11_POOL_DRIVER_MIGRATION",
    );
    expect(pool?.title).toBe("Connection pool driver migration");
    // Facts ordered old -> current; both persist (nothing overwritten).
    expect(pool?.facts.map((fact) => fact.status)).toEqual(["superseded", "current"]);
    expect(pool?.supersededAt).toBe("2026-07-01T03:00:00Z");
    // The post-migration incident chose the currently-valid fix, not the stale one.
    expect(pool?.incident?.expectedMemoryId).toBe("mem-f11-multiplexed-pool");
    expect(pool?.incident?.authorizedMemoryId).toBe("mem-f11-multiplexed-pool");
    expect(pool?.incident?.appliedCurrentlyValidFix).toBe(true);
    expect(pool?.incident?.appliedStaleFact).toBe(false);
  });

  it("summarizes fix actions into on-call-legible text", () => {
    expect(
      summarizeActions([
        { action_type: "set_config", target_service: "svc", params: { key: "db_pool_size", value: 80 } },
        { action_type: "restart_service", target_service: "svc", params: {} },
      ]),
    ).toBe("set db_pool_size → 80 · restart service");
    expect(
      summarizeActions([
        { action_type: "set_config", target_service: "svc", params: { key: "pool_multiplexing_enabled", value: true } },
      ]),
    ).toBe("enable pool_multiplexing_enabled");
    expect(
      summarizeActions([
        { action_type: "failover_dependency", target_service: "svc", params: { dependency_key: "storefront-cache", to_service: "cache-managed-primary" } },
      ]),
    ).toBe("fail over storefront-cache → cache-managed-primary");
  });

  it("rejects malformed inputs", () => {
    expect(temporalDriftViewFromReport({}, DRIFT_MEMORY_CORPUS)).toBeNull();
    expect(temporalDriftViewFromReport(PHASE_THREE_TEMPORAL_REPORT, {})).toBeNull();
  });
});
