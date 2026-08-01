import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { IncidentView } from "@/components/views/incident";
import type { ConsoleEvent } from "@/lib/events";
import { PHASE_TWO_EVENTS } from "@/lib/mock-events";

afterEach(cleanup);

const fixture = PHASE_TWO_EVENTS as readonly ConsoleEvent[];

describe("IncidentView", () => {
  it("renders the incident header from real event data", () => {
    const { container } = render(<IncidentView events={fixture} source="replay" />);
    const text = container.textContent ?? "";

    expect(text).toContain("SEV-1");
    expect(text).toContain("checkout-api");
    expect(text).toContain("CASE-2041");
    // headline insight
    expect(text).toContain("changed the action");
    // telemetry, formatted (p99 4200ms → 4.2 s, error rate 18.4%, deploy, burn)
    expect(text).toContain("4.2 s");
    expect(text).toContain("18.4%");
    expect(text).toContain("#5120");
    expect(text).toContain("16.2×");
  });

  it("renders the five response stages as a timeline", () => {
    const { container } = render(<IncidentView events={fixture} source="replay" />);
    const text = container.textContent ?? "";

    for (const stage of ["Perceive", "Recall", "Reason", "Act", "Record"]) {
      expect(text).toContain(stage);
    }
    // the transaction envelope bracket label
    expect(text).toContain("one transaction");
  });

  it("shows recall candidates as ranked bars with the cited winner", () => {
    const { container } = render(<IncidentView events={fixture} source="replay" />);
    const text = container.textContent ?? "";

    // every candidate memory id is a bar row label
    expect(text).toContain("ep-8842");
    expect(text).toContain("fact-2201");
    expect(text).toContain("RB-207");
    // the winner's similarity + component scores
    expect(text).toContain("0.94");
    expect(text).toContain("composite");
    expect(text).toContain("CASE-1878");
  });

  it("renders the one-transaction envelope with its statements", () => {
    const { container } = render(<IncidentView events={fixture} source="replay" />);
    const text = container.textContent ?? "";

    expect(text).toContain("BEGIN");
    expect(text).toContain("COMMIT");
    expect(text).toContain("8f2ab471c90e");
    expect(text).toContain("remediation_actions");
    expect(text).toContain("episodic_events");
  });

  it("renders record cross-agent freshness with zero stale reads", () => {
    const { container } = render(<IncidentView events={fixture} source="replay" />);
    const text = container.textContent ?? "";

    expect(text).toContain("ep-9217");
    expect(text).toContain("responder-02");
    expect(text).toContain("eu-west");
  });

  it("shows a calm empty state when no incident is on the stream", () => {
    const { container } = render(<IncidentView events={[]} source="replay" />);
    const text = container.textContent ?? "";
    expect(text).toContain("Waiting for an");
    expect(text).not.toContain("SEV-1");
  });

  it("renders an em-dash for absent telemetry, never a fabricated value", () => {
    const bare: ConsoleEvent[] = [
      {
        id: "evt-x",
        sequence: 1,
        occurredAt: "2026-07-30T03:01:00.000Z",
        caseId: "CASE-9",
        agent: { id: "responder-01", region: "us-east" },
        type: "incident",
        payload: {
          service: "orders-api",
          severity: "SEV-2",
          status: "open",
          summary: "elevated errors, no telemetry attached",
        },
      },
    ];
    const { container } = render(<IncidentView events={bare} source="replay" />);
    const text = container.textContent ?? "";
    expect(text).toContain("orders-api");
    // telemetry absent → em-dash present somewhere in the header readouts
    expect(text).toContain("—");
  });
});
