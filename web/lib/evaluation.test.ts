import { describe, expect, it } from "vitest";

import { evaluationEventFromReport } from "@/lib/evaluation";


describe("Phase 2 evaluation report transport", () => {
  it("maps the generated harness report into a console event", () => {
    const event = evaluationEventFromReport({
      generated_at: "2026-07-30T00:00:00Z",
      seed: 20260730,
      recall: { recall_at_10: 1 },
      arms: {
        cold_start: {
          summary: {
            median_mttr_seconds: 660,
            p90_mttr_seconds: 780,
            wrong_actions: 20,
            escalations: 3,
            failed_orders: 197,
            token_proxy_total: 27095,
          },
        },
        with_memory: {
          summary: {
            median_mttr_seconds: 240,
            p90_mttr_seconds: 420,
            wrong_actions: 0,
            escalations: 3,
            failed_orders: 157,
            token_proxy_total: 14031,
          },
        },
      },
      learning_curve: {
        cold_start: [{ occurrence: 1, median_mttr_seconds: 660 }],
        with_memory: [{ occurrence: 1, median_mttr_seconds: 300 }],
      },
    });

    expect(event?.payload.recallAt10).toBe(1);
    expect(event?.payload.cold.medianMttrSeconds).toBe(660);
    expect(event?.payload.memory.wrongActions).toBe(0);
    expect(event?.payload.learningCurve[0]).toEqual({
      occurrence: 1,
      coldMttrSeconds: 660,
      memoryMttrSeconds: 300,
    });
  });

  it("rejects incomplete reports", () => {
    expect(evaluationEventFromReport({ seed: 1 })).toBeNull();
  });
});
