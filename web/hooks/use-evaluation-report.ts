"use client";

import { useEffect, useState } from "react";

import type { EvaluationEvent } from "@/lib/events";
import { evaluationEventFromReport } from "@/lib/evaluation";
import { PHASE_TWO_EVENTS } from "@/lib/mock-events";

export type ReportSource = "live" | "replay";

/** The labelled replay fixture: the evaluation frame in the mock incident
 *  stream, whose retrieval numbers mirror the real v2 report. */
const REPLAY_EVALUATION = PHASE_TWO_EVENTS.find(
  (event): event is EvaluationEvent => event.type === "evaluation",
);

const evaluationEndpoint =
  process.env.NEXT_PUBLIC_POSTMORTEM_EVALUATION_URL ?? "/phase2-evaluation.json";

type EvaluationState = { event: EvaluationEvent | null; source: ReportSource };

/**
 * Loads the real Phase-2 evaluation report through the existing
 * `evaluationEventFromReport` mapper, falling back to the labelled replay
 * fixture so the surface is never blank (same camera-safe posture as the SSE
 * stream and the Phase-3 reports). Only real, measured retrieval numbers are
 * surfaced; decision-quality stays a `measured:false` flag the view renders as
 * "pending" — never a fabricated value.
 */
export function useEvaluationReport(): EvaluationState {
  const [state, setState] = useState<EvaluationState>({
    event: REPLAY_EVALUATION ?? null,
    source: "replay",
  });

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    void fetch(evaluationEndpoint, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`evaluation HTTP ${response.status}`);
        return response.json() as Promise<unknown>;
      })
      .then((report) => {
        if (!active) return;
        const event = evaluationEventFromReport(report);
        if (event) setState({ event, source: "live" });
      })
      .catch(() => {
        // Replay fixture already rendering; the live report is optional.
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  return state;
}
