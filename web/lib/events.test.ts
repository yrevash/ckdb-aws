import { describe, expect, it } from "vitest";

import { parseConsoleEvent, parseSsePayload } from "@/lib/events";
import { PHASE_TWO_EVENTS } from "@/lib/mock-events";

describe("console event transport", () => {
  it("accepts every deterministic Phase 2 event", () => {
    expect(PHASE_TWO_EVENTS.map(parseConsoleEvent)).toEqual(PHASE_TWO_EVENTS);
  });

  it("rejects malformed frames", () => {
    expect(parseConsoleEvent({ type: "recall" })).toBeNull();
    expect(parseConsoleEvent({ ...PHASE_TWO_EVENTS[0], type: "unknown" })).toBeNull();
    expect(parseSsePayload("not-json")).toBeNull();
  });

  it("rejects frames whose payload shape is wrong (deeper guard)", () => {
    const recall = PHASE_TWO_EVENTS.find((event) => event.type === "recall")!;
    // results the console maps over must be an array, not a bare object.
    expect(
      parseConsoleEvent({ ...recall, payload: { ...recall.payload, results: {} } }),
    ).toBeNull();

    const incident = PHASE_TWO_EVENTS[0];
    // a non-object payload can never satisfy the guard.
    expect(parseConsoleEvent({ ...incident, payload: "oops" })).toBeNull();
    // a missing required string field is rejected before it reaches render.
    expect(
      parseConsoleEvent({
        ...incident,
        payload: { severity: "SEV-1", status: "open", summary: "x" },
      }),
    ).toBeNull();
  });

  it("carries structured incident telemetry through the guard", () => {
    const incident = parseConsoleEvent(PHASE_TWO_EVENTS[0]);
    expect(incident?.type).toBe("incident");
    if (incident?.type === "incident") {
      // The telemetry the strip renders comes from the event payload, not the UI.
      expect(incident.payload.telemetry?.p99LatencyMs).toBe(4200);
      expect(incident.payload.telemetry?.errorRatePct).toBe(18.4);
      expect(incident.payload.telemetry?.deploy).toBe("#5120");
      expect(incident.payload.telemetry?.errorBudgetBurnRate).toBe(16.2);
    }
  });

  it("parses an SSE JSON data payload", () => {
    expect(parseSsePayload(JSON.stringify(PHASE_TWO_EVENTS[1]))).toEqual(
      PHASE_TWO_EVENTS[1],
    );
  });
});
