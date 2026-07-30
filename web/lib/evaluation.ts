import type { EvaluationEvent } from "@/lib/events";

type ArmSummary = {
  median_mttr_seconds: number;
  p90_mttr_seconds: number;
  wrong_actions: number;
  escalations: number;
  failed_orders: number;
  token_proxy_total: number;
};

type CurvePoint = {
  occurrence: number;
  median_mttr_seconds: number;
};

type PhaseTwoReport = {
  generated_at: string;
  seed: number;
  recall: { recall_at_10: number };
  arms: {
    cold_start: { summary: ArmSummary };
    with_memory: { summary: ArmSummary };
  };
  learning_curve: {
    cold_start: CurvePoint[];
    with_memory: CurvePoint[];
  };
};

export function evaluationEventFromReport(
  value: unknown,
  caseId = "PHASE-2-EVAL",
): EvaluationEvent | null {
  if (!value || typeof value !== "object") return null;
  const report = value as Partial<PhaseTwoReport>;
  const cold = report.arms?.cold_start?.summary;
  const memory = report.arms?.with_memory?.summary;
  const recallAt10 = report.recall?.recall_at_10;
  if (
    typeof report.seed !== "number" ||
    typeof report.generated_at !== "string" ||
    typeof recallAt10 !== "number" ||
    !cold ||
    !memory ||
    !Array.isArray(report.learning_curve?.cold_start) ||
    !Array.isArray(report.learning_curve?.with_memory)
  ) {
    return null;
  }

  const memoryByOccurrence = new Map(
    report.learning_curve.with_memory.map((point) => [
      point.occurrence,
      point.median_mttr_seconds,
    ]),
  );
  const learningCurve = report.learning_curve.cold_start.flatMap((point) => {
    const memoryMttrSeconds = memoryByOccurrence.get(point.occurrence);
    return typeof memoryMttrSeconds === "number"
      ? [
          {
            occurrence: point.occurrence,
            coldMttrSeconds: point.median_mttr_seconds,
            memoryMttrSeconds,
          },
        ]
      : [];
  });

  return {
    id: `phase2-eval-${report.seed}`,
    sequence: Number.MAX_SAFE_INTEGER,
    occurredAt: report.generated_at,
    caseId,
    agent: { id: "evaluation-harness", region: "us-east" },
    type: "evaluation",
    payload: {
      seed: report.seed,
      familyCount: 10,
      recallAt10,
      cold: arm(cold),
      memory: arm(memory),
      learningCurve,
    },
  };
}

function arm(summary: ArmSummary) {
  return {
    medianMttrSeconds: summary.median_mttr_seconds,
    p90MttrSeconds: summary.p90_mttr_seconds,
    wrongActions: summary.wrong_actions,
    escalations: summary.escalations,
    failedOrders: summary.failed_orders,
    tokenProxy: summary.token_proxy_total,
  };
}

