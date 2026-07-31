"use client";

import { useState } from "react";

import { IncidentConsole } from "@/components/incident-console";
import { ResiliencePanel } from "@/components/resilience-panel";
import { TemporalDriftPanel } from "@/components/temporal-drift-panel";
import { usePhase3Reports } from "@/hooks/use-phase3-reports";

type ViewId = "investigation" | "resilience" | "temporal";

const VIEWS: { id: ViewId; label: string; hint: string }[] = [
  { id: "investigation", label: "Investigation", hint: "Phase 2 · memory-lift" },
  { id: "resilience", label: "Resilience", hint: "RPO 0 · region kill" },
  { id: "temporal", label: "Temporal drift", hint: "facts evolve" },
];

export function PostmortemConsole() {
  const [view, setView] = useState<ViewId>("investigation");
  const { resilience, temporal } = usePhase3Reports();

  return (
    <div className="p3-app">
      <nav className="p3-nav" aria-label="Console view">
        <span className="p3-nav__brand" aria-hidden="true">
          P
        </span>
        <div className="p3-nav__tabs" role="tablist" aria-label="Console view">
          {VIEWS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={view === item.id}
              className={`p3-tab ${view === item.id ? "is-active" : ""}`}
              onClick={() => setView(item.id)}
            >
              <span className="p3-tab__label">{item.label}</span>
              <span className="p3-tab__hint">{item.hint}</span>
            </button>
          ))}
        </div>
      </nav>

      {view === "investigation" ? <IncidentConsole /> : null}
      {view === "resilience" ? (
        <ResiliencePanel view={resilience.view} source={resilience.source} />
      ) : null}
      {view === "temporal" ? (
        <TemporalDriftPanel view={temporal.view} source={temporal.source} />
      ) : null}
    </div>
  );
}
