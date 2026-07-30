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

  it("parses an SSE JSON data payload", () => {
    expect(parseSsePayload(JSON.stringify(PHASE_TWO_EVENTS[1]))).toEqual(
      PHASE_TWO_EVENTS[1],
    );
  });
});
