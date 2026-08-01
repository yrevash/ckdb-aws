"use client";

import { useState, type ReactNode } from "react";

import { ErrorBoundary } from "@/components/error-boundary";
import { Overview } from "@/components/views/overview";
import { Incident } from "@/components/views/incident";
import { Resilience } from "@/components/views/resilience";
import { Memory } from "@/components/views/memory";
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
  /** right-aligned mono meta. */
  meta?: string;
};

const NAV: NavEntry[] = [
  { id: "overview", label: "Overview", glyph: <IconOverview className="nav-item__glyph" />, meta: "proven · pending" },
  { id: "incident", label: "Incident", glyph: <IconIncident className="nav-item__glyph" />, meta: "live" },
  { id: "resilience", label: "Resilience", glyph: <IconResilience className="nav-item__glyph" />, meta: "RPO 0 · RTO 3.9s" },
  { id: "memory", label: "Memory & Retrieval", glyph: <IconMemory className="nav-item__glyph" />, meta: "recall@1 0.85" },
];

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
              {item.meta ? <span className="nav-item__meta">{item.meta}</span> : null}
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
          ) : view === "incident" ? (
            <Incident />
          ) : view === "resilience" ? (
            <Resilience />
          ) : (
            <Memory />
          )}
        </ErrorBoundary>
      </main>
    </div>
  );
}
