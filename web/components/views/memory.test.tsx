import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Memory } from "@/components/views/memory";

/**
 * Render smoke + honesty test for the Memory & Retrieval view. The eval + phase3
 * hooks fetch live artifacts and fall back to the labelled replay fixtures; here
 * we force the replay path (fetch rejects) and assert the view renders the real
 * replayed numbers and — crucially — the honest "pending real-agent run" panel
 * with no fabricated decision-quality figure.
 */
describe("Memory view", () => {
  beforeEach(() => {
    // Force the replay fixtures: the live report fetch simply rejects.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline in test")));
    // jsdom has no rAF/matchMedia; stub so the count-up effect is inert.
    vi.stubGlobal("requestAnimationFrame", () => 0);
    vi.stubGlobal("cancelAnimationFrame", () => {});
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: false, addEventListener() {}, removeEventListener() {} }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("leads with the honest retrieval insight", () => {
    render(<Memory />);
    expect(screen.getByText("Retrieval is genuinely good")).toBeTruthy();
    expect(screen.getByText("The corpus is adversarial on purpose")).toBeTruthy();
    // recall@1 replay value, rendered statically in the ranked bars.
    expect(screen.getAllByText("0.85").length).toBeGreaterThan(0);
    // the 9 planted hard negatives are called out explicitly.
    expect(screen.getByText("9 hard negatives")).toBeTruthy();
  });

  it("shows abstention / near-miss correctness as passing", () => {
    render(<Memory />);
    expect(screen.getAllByText("Abstention accuracy").length).toBeGreaterThan(0);
    expect(screen.getByText("Near-miss safe rejection")).toBeTruthy();
    expect(screen.getByText(/close-but-wrong rejected/)).toBeTruthy();
  });

  it("renders the temporal-drift timeline for each fact family", () => {
    render(<Memory />);
    expect(screen.getByText("Facts evolve, not overwrite")).toBeTruthy();
    expect(screen.getAllByText("Connection pool driver migration").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Cache topology migration").length).toBeGreaterThan(0);
    // both the superseded and the currently-valid fact are labelled (per family).
    expect(screen.getAllByText("currently valid").length).toBeGreaterThan(0);
    expect(screen.getAllByText("superseded").length).toBeGreaterThan(0);
    // the agent chose the current fix on the migrated incident.
    expect(screen.getAllByText("chose current fix").length).toBeGreaterThan(0);
  });

  it("keeps decision quality honestly pending — no fabricated number", () => {
    render(<Memory />);
    expect(screen.getByText("Decision quality waits for the real agent")).toBeTruthy();
    expect(screen.getByText("pending real-agent run")).toBeTruthy();
    expect(screen.getByText(/MTTR delta · —/)).toBeTruthy();
    expect(screen.getByText(/wrong-action rate · —/)).toBeTruthy();
  });
});
