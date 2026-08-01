"use client";

import { useState, type ReactNode } from "react";

import { ErrorBoundary } from "@/components/error-boundary";
import { Overview } from "@/components/views/overview";
import { Placeholder } from "@/components/views/placeholder";
import { ThemeToggle } from "@/components/ui";
import {
  IconIncident,
  IconMemory,
  IconOverview,
  IconResilience,
} from "@/components/ui/icons";

type ViewId = "overview" | "incident" | "resilience" | "memory";

type NavEntry = {
  id: ViewId;
  label: string;
  glyph: ReactNode;
  /** right-aligned mono meta (built views) or the "soon" pill (stubs). */
  meta?: string;
  ready: boolean;
};

const NAV: NavEntry[] = [
  { id: "overview", label: "Overview", glyph: <IconOverview className="nav-item__glyph" />, meta: "proven · pending", ready: true },
  { id: "incident", label: "Incident", glyph: <IconIncident className="nav-item__glyph" />, ready: false },
  { id: "resilience", label: "Resilience", glyph: <IconResilience className="nav-item__glyph" />, ready: false },
  { id: "memory", label: "Memory & Retrieval", glyph: <IconMemory className="nav-item__glyph" />, ready: false },
];

const STUBS: Record<Exclude<ViewId, "overview">, { title: string; what: string; sources: string[] }> = {
  incident: {
    title: "Incident",
    what: "The live investigation story: perceive → recall → reason → act → record, with the ranked recall candidates and the one-transaction envelope that show memory changed the action.",
    sources: ["events.ts (SSE / replay)"],
  },
  resilience: {
    title: "Resilience",
    what: "The full failover proof: region topology with the killed region, leaseholder distribution before/during/after, and every probe in detail.",
    sources: ["resilience.ts", "phase3-resilience"],
  },
  memory: {
    title: "Memory & Retrieval",
    what: "The evaluation insights in depth and the temporal-drift timeline — facts evolving, not overwriting, with the agent choosing the currently-valid fix.",
    sources: ["evaluation.ts", "temporal-drift.ts"],
  },
};

export function AppShell() {
  const [view, setView] = useState<ViewId>("overview");
  const active = NAV.find((n) => n.id === view) ?? NAV[0];

  return (
    <div className="shell">
      <nav className="rail" aria-label="Primary">
        <div className="rail__brand">
          <span className="rail__mark" aria-hidden="true">
            <span>P</span>
          </span>
          <span className="rail__wordmark">
            <b>Postmortem</b>
            <small>Incident console</small>
          </span>
        </div>

        <div className="rail__nav" role="tablist" aria-label="Views">
          <div className="rail__section">Console</div>
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={view === item.id}
              className={`nav-item${view === item.id ? " is-active" : ""}`}
              onClick={() => setView(item.id)}
            >
              {item.glyph}
              <span>{item.label}</span>
              {item.ready ? (
                item.meta ? <span className="nav-item__meta">{item.meta}</span> : null
              ) : (
                <span className="nav-item__soon">soon</span>
              )}
            </button>
          ))}
        </div>

        <div className="rail__foot">
          <ThemeToggle />
        </div>
      </nav>

      <main className="main" aria-label={active.label}>
        <ErrorBoundary surface={active.label}>
          {view === "overview" ? (
            <Overview />
          ) : (
            <Placeholder
              glyph={active.glyph}
              title={STUBS[view].title}
              what={STUBS[view].what}
              sources={STUBS[view].sources}
            />
          )}
        </ErrorBoundary>
      </main>
    </div>
  );
}
