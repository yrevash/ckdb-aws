"use client";

import { useEffect, useState } from "react";

import {
  PHASE_THREE_RESILIENCE,
  resilienceViewFromReport,
  type ResilienceView,
} from "@/lib/resilience";
import {
  DRIFT_MEMORY_CORPUS,
  PHASE_THREE_TEMPORAL,
  temporalDriftViewFromReport,
  type TemporalDriftView,
} from "@/lib/temporal-drift";

export type ReportSource = "live" | "replay";

type ResilienceState = { view: ResilienceView; source: ReportSource };
type TemporalState = { view: TemporalDriftView; source: ReportSource };

const resilienceEndpoint =
  process.env.NEXT_PUBLIC_POSTMORTEM_RESILIENCE_URL ?? "/phase3-resilience.json";
const temporalEndpoint =
  process.env.NEXT_PUBLIC_POSTMORTEM_TEMPORAL_URL ?? "/phase3-temporal.json";

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`report HTTP ${response.status}`);
  return (await response.json()) as unknown;
}

/**
 * Loads the live Phase 3 telemetry artifacts, falling back to the embedded
 * deterministic replay fixtures — the same camera-safe posture the SSE incident
 * stream uses (see use-console-events.ts). The replay fixtures render
 * immediately so the surface is never blank while a live report resolves.
 */
export function usePhase3Reports() {
  const [resilience, setResilience] = useState<ResilienceState>({
    view: PHASE_THREE_RESILIENCE,
    source: "replay",
  });
  const [temporal, setTemporal] = useState<TemporalState>({
    view: PHASE_THREE_TEMPORAL,
    source: "replay",
  });

  useEffect(() => {
    let active = true;

    void fetchJson(resilienceEndpoint)
      .then((report) => {
        const view = resilienceViewFromReport(report);
        if (active && view) setResilience({ view, source: "live" });
      })
      .catch(() => {
        // Replay fixture already rendering; the live report is optional.
      });

    void fetchJson(temporalEndpoint)
      .then((report) => {
        // The eval report carries the temporal_drift block; the corpus is a
        // stable fixture the console ships with.
        const block =
          report && typeof report === "object" && "temporal_drift" in report
            ? (report as { temporal_drift: unknown }).temporal_drift
            : report;
        const view = temporalDriftViewFromReport(block, DRIFT_MEMORY_CORPUS);
        if (active && view) setTemporal({ view, source: "live" });
      })
      .catch(() => {
        // Replay fixture already rendering.
      });

    return () => {
      active = false;
    };
  }, []);

  return { resilience, temporal };
}
