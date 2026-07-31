"use client";

import type { ReportSource } from "@/hooks/use-phase3-reports";
import type { DriftFamilyView, TemporalDriftView } from "@/lib/temporal-drift";

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(iso));
}

function SourceChip({ source }: { source: ReportSource }) {
  return (
    <span className={`res-source res-source--${source}`}>
      <span className="res-source__dot" aria-hidden="true" />
      {source === "live" ? "Live telemetry" : "Deterministic replay"}
    </span>
  );
}

function FamilyTrack({ family }: { family: DriftFamilyView }) {
  const incident = family.incident;
  const chosenId = incident?.authorizedMemoryId;

  return (
    <article className="drift-family">
      <div className="drift-family__head">
        <span className="eyebrow">Semantic fact · {family.familyId}</span>
        <h2>{family.title}</h2>
      </div>

      <ol className="drift-track" aria-label={`Valid-time transition for ${family.title}`}>
        {family.facts.map((fact, index) => {
          const isChosen = fact.memoryId === chosenId;
          return (
            <li
              className={`drift-fact drift-fact--${fact.status} ${isChosen ? "is-chosen" : ""}`}
              key={fact.memoryId}
            >
              {index > 0 ? (
                <span className="drift-supersede" aria-hidden="true">
                  <span className="drift-supersede__mark">◆</span>
                  <span className="drift-supersede__label">environment migrated</span>
                </span>
              ) : null}
              <div className="drift-fact__card">
                <div className="drift-fact__top">
                  <span className={`drift-badge drift-badge--${fact.status}`}>
                    {fact.status === "current" ? "currently valid" : "superseded"}
                  </span>
                  <code className="drift-fact__id">{fact.memoryId}</code>
                </div>
                <strong className="drift-fact__action">{fact.actionSummary}</strong>
                <p className="drift-fact__text">{fact.text}</p>
                <span className="drift-fact__window">
                  valid {formatDate(fact.validFrom)} →{" "}
                  {fact.validTo ? formatDate(fact.validTo) : "now"}
                  {isChosen ? <em className="drift-fact__pick"> · agent applied this</em> : null}
                </span>
              </div>
            </li>
          );
        })}
      </ol>

      {incident ? (
        <div className={`drift-decision ${incident.appliedCurrentlyValidFix ? "is-correct" : "is-stale"}`}>
          <div className="drift-decision__row">
            <span className="eyebrow">Decision at incident time</span>
            <span className="drift-decision__when">{formatDate(incident.observedAt)}</span>
          </div>
          <p className="drift-decision__verdict">
            {incident.appliedCurrentlyValidFix ? (
              <>
                <span aria-hidden="true">✓</span> Agent read the fact valid <strong>now</strong> and
                applied the currently-valid fix. The superseded fix stayed on record —
                <strong> nothing was overwritten</strong>.
              </>
            ) : (
              <>
                <span aria-hidden="true">✕</span> Agent applied a superseded fix.
              </>
            )}
          </p>
          <div className="drift-decision__facts">
            <span>
              expected <code>{incident.expectedMemoryId ?? "—"}</code>
            </span>
            <span>
              applied <code>{incident.authorizedMemoryId ?? "—"}</code>
            </span>
            <span>
              stale fact applied <strong>{incident.appliedStaleFact ? "yes" : "no"}</strong>
            </span>
            {incident.mttrSeconds !== null ? (
              <span>
                MTTR <strong>{incident.mttrSeconds}s</strong>
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}

export function TemporalDriftPanel({
  view,
  source,
}: {
  view: TemporalDriftView;
  source: ReportSource;
}) {
  return (
    <section className="drift-panel" aria-labelledby="drift-heading">
      <header className="res-panel__head">
        <div>
          <span className="eyebrow">Bitemporal memory · Track B</span>
          <h1 id="drift-heading">Facts evolve, not overwrite</h1>
          <p className="res-lede">
            A fix that was correct becomes wrong after the platform changes. The
            old fact is not deleted — it is closed in business time and a new one
            opens. Recall returns the fact valid at the incident&apos;s decision time.
          </p>
        </div>
        <div className="res-head-meta">
          <SourceChip source={source} />
          <span className={`res-verdict ${view.meetsTarget ? "is-pass" : "is-fail"}`}>
            <span aria-hidden="true">{view.meetsTarget ? "✓" : "✕"}</span> meets target
          </span>
        </div>
      </header>

      <div className="drift-scorecard">
        <div className="drift-metric drift-metric--gold">
          <strong>{view.temporalValidityAccuracy.toFixed(2)}</strong>
          <span>temporal-validity accuracy</span>
          <em>target ≥ {view.targetAccuracy.toFixed(2)}</em>
        </div>
        <div className="drift-metric">
          <strong>{view.staleFactApplications}</strong>
          <span>stale-fact applications</span>
          <em>target 0</em>
        </div>
        <div className="drift-metric">
          <strong>{view.incidentsEvaluated}</strong>
          <span>drift incidents scored</span>
          <em>{view.families.length} families</em>
        </div>
      </div>

      <div className="drift-families">
        {view.families.map((family) => (
          <FamilyTrack key={family.familyId} family={family} />
        ))}
      </div>
    </section>
  );
}
