import { describe, expect, it } from "vitest";

import { evaluationEventFromReport } from "@/lib/evaluation";

describe("Phase 2 evaluation report transport (v2, honest)", () => {
  it("maps the real retrieval metrics and the pending decision-quality flag", () => {
    const event = evaluationEventFromReport({
      schema_version: "postmortem-eval-v2",
      generated_at: "2026-07-31T00:00:00Z",
      seed: 20260731,
      retrieval: {
        recall_at_1: 0.846154,
        recall_at_5: 1.0,
        recall_at_10: 1.0,
        ndcg_at_10: 0.94322,
        hard_negative_count: 9,
        status: "measured",
      },
      decision_quality: { measured: false, status: "pending_real_agent_run" },
      temporal_validity: { status: "measured" },
    });

    // Real retrieval surfaced (recall@1 < 1.0 by design).
    expect(event?.payload.retrieval.recallAt1).toBeCloseTo(0.8462, 3);
    expect(event?.payload.retrieval.recallAt10).toBe(1);
    expect(event?.payload.retrieval.ndcgAt10).toBeCloseTo(0.9432, 3);
    expect(event?.payload.retrieval.hardNegativeCount).toBe(9);
    // Decision quality is NOT measured yet — never a fabricated number.
    expect(event?.payload.decisionQualityMeasured).toBe(false);
  });

  it("rejects a report missing the real retrieval section", () => {
    expect(evaluationEventFromReport({ seed: 1 })).toBeNull();
    expect(
      evaluationEventFromReport({
        generated_at: "2026-07-31T00:00:00Z",
        seed: 1,
        decision_quality: { measured: false },
      }),
    ).toBeNull();
  });
});
