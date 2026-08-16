import type { EvaluationEvent } from "@/lib/events";

// Consumes the honest v2 evaluation report (schema_version "postmortem-eval-v2").
// Only real, measured retrieval numbers are surfaced; decision-quality (MTTR,
// wrong-action rate) is carried as a boolean "measured" flag and is false until
// the real reasoning agent runs — the console renders "pending", never a number.
type RetrievalSection = {
  recall_at_1: number;
  recall_at_5?: number;
  recall_at_10: number;
  ndcg_at_10: number;
  hard_negative_count?: number;
};

type EvalReportV2 = {
  generated_at: string;
  seed: number;
  retrieval: RetrievalSection;
  decision_quality: { measured: boolean };
};

export function evaluationEventFromReport(
  value: unknown,
  caseId = "PHASE-2-EVAL",
): EvaluationEvent | null {
  if (!value || typeof value !== "object") return null;
  const report = value as Partial<EvalReportV2>;
  const retrieval = report.retrieval;
  if (
    typeof report.seed !== "number" ||
    typeof report.generated_at !== "string" ||
    !retrieval ||
    typeof retrieval.recall_at_1 !== "number" ||
    typeof retrieval.recall_at_10 !== "number" ||
    typeof retrieval.ndcg_at_10 !== "number" ||
    typeof report.decision_quality?.measured !== "boolean"
  ) {
    return null;
  }

  return {
    id: `phase2-eval-${report.seed}`,
    sequence: Number.MAX_SAFE_INTEGER,
    occurredAt: report.generated_at,
    caseId,
    // the primary region the harness runs against (db/bootstrap/010_multiregion.sql
    // PRIMARY REGION; matches POSTMORTEM_AWS_REGION's default in backend config.py)
    agent: { id: "evaluation-harness", region: "us-east-1" },
    type: "evaluation",
    payload: {
      seed: report.seed,
      retrieval: {
        recallAt1: retrieval.recall_at_1,
        recallAt5:
          typeof retrieval.recall_at_5 === "number"
            ? retrieval.recall_at_5
            : retrieval.recall_at_10,
        recallAt10: retrieval.recall_at_10,
        ndcgAt10: retrieval.ndcg_at_10,
        hardNegativeCount:
          typeof retrieval.hard_negative_count === "number"
            ? retrieval.hard_negative_count
            : 0,
      },
      decisionQualityMeasured: report.decision_quality.measured,
    },
  };
}
