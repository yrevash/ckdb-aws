"use client";

import { useMemo, useState } from "react";

import { useConsoleEvents } from "@/hooks/use-console-events";
import type {
  ActEvent,
  ConsoleEvent,
  EvaluationEvent,
  FailoverEvent,
  IncidentEvent,
  RecallEvent,
  ReasonEvent,
  RecordEvent,
  Region,
  TransactionEvent,
} from "@/lib/events";

type FeedCase = {
  id: string;
  service: string;
  severity?: string;
  region?: string;
  status: "active" | "recalled";
};

/**
 * Build the incident feed from real stream data only: the active case comes
 * from the incident event, prior cases come from what recall actually returned
 * (result.sourceCaseId). Nothing is invented — an empty stream yields an empty
 * feed (Reality Charter R6).
 */
function feedCasesFrom(incident?: IncidentEvent, recall?: RecallEvent): FeedCase[] {
  const cases: FeedCase[] = [];
  const seen = new Set<string>();

  if (incident) {
    cases.push({
      id: incident.caseId,
      service: incident.payload.service,
      severity: incident.payload.severity,
      region: incident.agent.region,
      status: "active",
    });
    seen.add(incident.caseId);
  }

  for (const result of recall?.payload.results ?? []) {
    if (!result.sourceCaseId || seen.has(result.sourceCaseId)) continue;
    seen.add(result.sourceCaseId);
    cases.push({
      id: result.sourceCaseId,
      service: result.scope?.service ?? "—",
      status: "recalled",
    });
  }

  return cases;
}

function eventOfType<TType extends ConsoleEvent["type"]>(
  events: ConsoleEvent[],
  type: TType,
): Extract<ConsoleEvent, { type: TType }> | undefined {
  return events.find(
    (event): event is Extract<ConsoleEvent, { type: TType }> => event.type === type,
  );
}

function formatTime(isoDate: string) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(new Date(isoDate));
}

/** p99 latency: milliseconds under a second, otherwise seconds with one decimal. */
function formatLatency(ms?: number) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function formatPercent(pct?: number) {
  if (typeof pct !== "number" || !Number.isFinite(pct)) return "—";
  return `${pct}%`;
}

function formatBurn(rate?: number) {
  if (typeof rate !== "number" || !Number.isFinite(rate)) return "—";
  return `${rate}×`;
}

/** Format an mm:ss elapsed span from a non-negative number of seconds. */
function formatElapsed(totalSeconds: number | null) {
  if (totalSeconds === null || !Number.isFinite(totalSeconds) || totalSeconds < 0) return "—";
  const rounded = Math.floor(totalSeconds);
  const minutes = Math.floor(rounded / 60);
  const seconds = rounded % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** Render a tool-call argument value: strings quoted, numbers/booleans bare. */
function formatArgument(value: string | number | boolean) {
  return typeof value === "string" ? `"${value}"` : String(value);
}

function Glyph({
  children,
  tone = "neutral",
}: {
  children: string;
  tone?: "neutral" | "recall" | "action" | "healthy";
}) {
  return (
    <span className={`glyph glyph--${tone}`} aria-hidden="true">
      {children}
    </span>
  );
}

function StreamIndicator({
  status,
}: {
  status: "connecting" | "live" | "replay" | "paused";
}) {
  const labels = {
    connecting: "Connecting",
    live: "Live agent stream",
    replay: "Deterministic replay",
    paused: "Replay paused",
  };

  return (
    <span className={`stream-status stream-status--${status}`}>
      <span className="stream-status__dot" aria-hidden="true" />
      {labels[status]}
    </span>
  );
}

function SystemStateBar({
  streamStatus,
  incident,
  failover,
  regions,
}: {
  streamStatus: "connecting" | "live" | "replay" | "paused";
  incident?: IncidentEvent;
  failover?: FailoverEvent;
  regions: Region[];
}) {
  const clusterState = failover?.payload.clusterState;
  const rtoMs = failover?.payload.rtoMs ?? null;

  return (
    <header className="system-bar">
      <div className="brand-block">
        <span className="brand-mark" aria-hidden="true">
          P
        </span>
        <span>
          <span className="eyebrow">Incident memory system</span>
          <span className="brand-name">postmortem</span>
        </span>
      </div>

      <div className="active-case">
        <span className="case-kicker">Active case</span>
        <strong>{incident?.caseId ?? "—"}</strong>
        <span className="case-service">{incident?.payload.service ?? "—"}</span>
        <span className="severity-badge">
          <span aria-hidden="true">◆</span> {incident?.payload.severity ?? "—"}
        </span>
      </div>

      <div className="system-state" aria-label="CockroachDB system state">
        <div className="regions" aria-label="Regions observed in the incident stream">
          {regions.length ? (
            regions.map((region) => {
              const down =
                failover?.payload.affectedRegion === region &&
                failover.payload.regionState !== "healthy";
              return (
                <span className={`region ${down ? "region--down" : ""}`} key={region}>
                  <span className="region__dot" aria-hidden="true" />
                  {region}
                </span>
              );
            })
          ) : (
            <span className="region">
              <span className="region__dot" aria-hidden="true" />—
            </span>
          )}
        </div>
        <div className="thesis-metrics">
          <span className="metric">
            <span className="metric__label">RPO</span>
            <strong>{failover ? failover.payload.rpoRows : "—"}</strong>
            <span className="metric__unit">rows</span>
          </span>
          <span className="metric">
            <span className="metric__label">RTO</span>
            <strong>{rtoMs !== null ? `${rtoMs}ms` : "—"}</strong>
          </span>
          <span className="cluster-health">
            <span aria-hidden="true">●</span> {clusterState ?? "—"}
          </span>
        </div>
      </div>

      <StreamIndicator status={streamStatus} />
    </header>
  );
}

function IncidentFeed({
  incident,
  recall,
}: {
  incident?: IncidentEvent;
  recall?: RecallEvent;
}) {
  const [filter, setFilter] = useState<"all" | "sev1" | "active">("all");
  const cases = feedCasesFrom(incident, recall);

  const visibleCases = cases.filter((item) => {
    if (filter === "sev1") return item.severity === "SEV-1";
    if (filter === "active") return item.status === "active";
    return true;
  });

  const recalledCase = cases.find((item) => item.status === "recalled");

  return (
    <aside className="rail incident-rail" aria-labelledby="incident-feed-heading">
      <div className="rail-heading">
        <span>
          <span className="eyebrow">Live ledger</span>
          <h2 id="incident-feed-heading">Incident feed</h2>
        </span>
        <span className="count-chip">{visibleCases.length}</span>
      </div>

      <div className="case-list">
        {visibleCases.length ? (
          visibleCases.map((item) => (
            <button
              className={`case-row case-row--${item.status}`}
              type="button"
              key={item.id}
              aria-current={item.status === "active" ? "true" : undefined}
            >
              <span className="case-row__rail" aria-hidden="true" />
              <span className="case-row__topline">
                <strong>{item.id}</strong>
                <span className="case-row__state">
                  {item.status === "active" ? "active" : "memory source"}
                </span>
              </span>
              <span className="case-row__service">{item.service}</span>
              <span className="case-row__meta">
                <span>{item.severity ?? "—"}</span>
                <span>{item.region ?? "—"}</span>
              </span>
            </button>
          ))
        ) : (
          <div className="memory-empty">
            <span className="memory-empty__glyph" aria-hidden="true">
              ◇
            </span>
            <strong>No cases yet</strong>
            <p>The active case and any recalled prior cases will appear here.</p>
          </div>
        )}
      </div>

      <fieldset className="filters">
        <legend className="eyebrow">Filter cases</legend>
        <div className="filter-options">
          {(
            [
              ["all", "All"],
              ["sev1", "SEV-1"],
              ["active", "Active"],
            ] as const
          ).map(([value, label]) => (
            <button
              type="button"
              className={filter === value ? "filter-chip is-active" : "filter-chip"}
              aria-pressed={filter === value}
              key={value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>

      {recalledCase ? (
        <div className="rail-footnote">
          <Glyph tone="recall">◇</Glyph>
          <span>
            <strong>{recalledCase.id}</strong> surfaced by recall—the prior case is
            evidence, not hidden context.
          </span>
        </div>
      ) : null}
    </aside>
  );
}

function EmptyStep({ label }: { label: string }) {
  return (
    <div className="pending-step">
      <span className="pending-step__marker" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

function Investigation({
  incident,
  reason,
  action,
  transaction,
  hasRecall,
  elapsedSeconds,
}: {
  incident?: IncidentEvent;
  reason?: ReasonEvent;
  action?: ActEvent;
  transaction?: TransactionEvent;
  hasRecall: boolean;
  elapsedSeconds: number | null;
}) {
  const [approved, setApproved] = useState(false);
  const telemetry = incident?.payload.telemetry;
  const argEntries = action ? Object.entries(action.payload.arguments) : [];

  return (
    <main className="investigation" id="investigation" aria-labelledby="investigation-heading">
      <div className="investigation__header">
        <div>
          <span className="eyebrow">{incident?.caseId ?? "—"} · live investigation</span>
          <h1 id="investigation-heading">{incident?.payload.service ?? "Awaiting incident"}</h1>
        </div>
        <span className="elapsed">
          <span aria-hidden="true">◷</span> {formatElapsed(elapsedSeconds)} elapsed
        </span>
      </div>

      <div className="transcript" aria-live="polite">
        {incident ? (
          <article className="alert-card">
            <div className="event-marker event-marker--alert" aria-hidden="true">
              !
            </div>
            <div className="alert-card__content">
              <div className="event-meta">
                <span className="event-label event-label--alert">Alert raised</span>
                <time dateTime={incident.occurredAt}>{formatTime(incident.occurredAt)}</time>
              </div>
              <h3>{incident.payload.summary}</h3>
              <div className="telemetry-strip">
                <span>
                  p99 <strong>{formatLatency(telemetry?.p99LatencyMs)}</strong>
                </span>
                <span>
                  errors <strong>{formatPercent(telemetry?.errorRatePct)}</strong>
                </span>
                <span>
                  deploy <strong>{telemetry?.deploy ?? "—"}</strong>
                </span>
                <span>
                  burn <strong>{formatBurn(telemetry?.errorBudgetBurnRate)}</strong>
                </span>
              </div>
            </div>
          </article>
        ) : (
          <EmptyStep label="Waiting for the incident conductor…" />
        )}

        {hasRecall ? (
          <div className="recall-progress">
            <span className="recall-progress__spark" aria-hidden="true">
              ✦
            </span>
            <span>Institutional memory recalled</span>
            <span className="recall-progress__rule" aria-hidden="true" />
          </div>
        ) : incident ? (
          <div className="recall-progress is-loading">
            <span className="recall-progress__spark" aria-hidden="true">
              ✦
            </span>
            <span>Searching prior cases through C-SPANN + MCP…</span>
          </div>
        ) : null}

        {reason ? (
          <article className="agent-message">
            <div className="agent-message__avatar" aria-hidden="true">
              P
            </div>
            <div className="agent-message__body">
              <div className="event-meta">
                <span className="event-label">Postmortem · responder-01</span>
                <time dateTime={reason.occurredAt}>{formatTime(reason.occurredAt)}</time>
              </div>
              <p>{reason.payload.message}</p>
              <div className="citations" aria-label="Reasoning citations">
                {reason.payload.citedMemoryIds.map((id) => (
                  <span key={id}>
                    <Glyph tone="recall">◇</Glyph> memory/{id}
                  </span>
                ))}
                {reason.payload.citedRunbookIds.map((id) => (
                  <span key={id}>
                    <Glyph tone="recall">↳</Glyph> runbook/{id}
                  </span>
                ))}
              </div>
              <span className="recall-thread recall-thread--origin" aria-hidden="true">
                <span />
              </span>
            </div>
          </article>
        ) : hasRecall ? (
          <EmptyStep label="Reasoning from the recalled evidence…" />
        ) : null}

        {action ? (
          <article className="action-card">
            <div className="action-card__header">
              <span className="action-title">
                <Glyph tone="action">▶</Glyph>
                Action · {transaction ? "committed" : approved ? "running" : action.payload.status}
              </span>
              <button
                className="run-button"
                type="button"
                disabled={approved || Boolean(transaction)}
                onClick={() => setApproved(true)}
              >
                {transaction ? "Committed ✓" : approved ? "Running…" : "Approve & run"}
              </button>
            </div>
            <div className="tool-call">
              <span className="tool-call__prompt" aria-hidden="true">
                ›
              </span>
              <code>
                {action.payload.tool}
                <span>(</span>
                {argEntries.length ? (
                  argEntries.map(([key, value], index) => (
                    <span key={key}>
                      {index > 0 ? ", " : ""}
                      {key}={formatArgument(value)}
                    </span>
                  ))
                ) : (
                  <span>—</span>
                )}
                <span>)</span>
              </code>
            </div>
            <div className="action-facts">
              <span>
                <Glyph tone="recall">◇</Glyph> cites {action.payload.citedMemoryId}
              </span>
              <span>
                <Glyph tone="action">●</Glyph> operational + memory write
              </span>
              <span>human approval required</span>
            </div>

            {transaction ? <TransactionEnvelope transaction={transaction} /> : null}
          </article>
        ) : reason ? (
          <EmptyStep label="Preparing a provenance-gated remediation…" />
        ) : null}
      </div>

      <details className="topology-drawer">
        <summary>
          <span>
            <Glyph tone="action">⌁</Glyph>
            System state / service topology
          </span>
          <span className="summary-hint">4 services · 1 degraded</span>
        </summary>
        <div className="topology" aria-label="Checkout dependency chain">
          {["checkout", "payments", "fraud", "ledger"].map((service, index) => (
            <div className="topology__step" key={service}>
              <span className={index === 0 ? "service-node is-degraded" : "service-node"}>
                <span className="service-node__dot" aria-hidden="true" />
                {service}
              </span>
              {index < 3 ? <span aria-hidden="true">→</span> : null}
            </div>
          ))}
        </div>
      </details>
    </main>
  );
}

function TransactionEnvelope({ transaction }: { transaction: TransactionEvent }) {
  return (
    <section className="transaction-envelope" aria-labelledby="transaction-heading">
      <div className="transaction-envelope__heading">
        <span id="transaction-heading">Transaction envelope</span>
        <strong>1 txn · 1 commit</strong>
      </div>
      <code className="transaction-line">BEGIN;</code>
      <ol className="statement-list">
        {transaction.payload.statements.map((statement) => (
          <li className={`statement statement--${statement.role}`} key={statement.target}>
            <span className="statement__dot" aria-hidden="true" />
            <span className="statement__role">{statement.role}</span>
            <code>
              {statement.operation} {statement.target}
            </code>
            <span className="statement__summary">{statement.summary}</span>
          </li>
        ))}
      </ol>
      <div className="commit-line">
        <code>COMMIT;</code>
        <span>
          txn={transaction.payload.transactionId} ·{" "}
          {transaction.payload.committedAt
            ? formatTime(transaction.payload.committedAt)
            : "pending"}{" "}
          ✓
        </span>
      </div>
      <p>same store · they can never disagree</p>
    </section>
  );
}

function SimilarityDial({ score }: { score: number }) {
  const degrees = Math.round(score * 360);
  return (
    <div
      className="similarity-dial"
      role="meter"
      aria-label={`Similarity ${Math.round(score * 100)} percent`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(score * 100)}
      style={{ "--dial-value": `${degrees}deg` } as React.CSSProperties}
    >
      <span>
        <strong>{score.toFixed(2)}</strong>
        <small>match</small>
      </span>
    </div>
  );
}

function RecallEvidenceCard({ recall }: { recall: RecallEvent }) {
  const result = recall.payload.results[0];

  if (!result) return null;

  return (
    <article className="memory-card memory-card--recalled">
      <span className="recall-thread recall-thread--target" aria-hidden="true">
        <span />
      </span>
      <div className="memory-card__eyebrow">
        <span className="memory-event-label">
          <span aria-hidden="true">◆</span> recalled
        </span>
        <span>{recall.payload.durationMs}ms</span>
      </div>
      <div className="memory-card__case">
        <div>
          <strong>{result.sourceCaseId}</strong>
          <span>{result.memoryKind ?? "episodic"} memory · accepted</span>
        </div>
        <SimilarityDial score={result.similarity} />
      </div>
      <p>{result.summary}</p>
      <div className="memory-outcome">
        <span className="eyebrow">Outcome that worked</span>
        <strong>{result.successfulAction}</strong>
      </div>
      {result.runbookId ? (
        <button className="runbook-chip" type="button">
          <span>↳</span> {result.runbookId}
          <span className="runbook-chip__action">inspect</span>
        </button>
      ) : null}
      <div className="memory-card__footer">
        <span>{recall.payload.provider}</span>
        <span>scope: {result.scope.service}</span>
      </div>
      {result.score ? (
        <dl className="score-breakdown" aria-label="Recall reranking evidence">
          <div>
            <dt>vector</dt>
            <dd>{result.score.vector.toFixed(2)}</dd>
          </div>
          <div>
            <dt>scope</dt>
            <dd>{result.score.scope.toFixed(2)}</dd>
          </div>
          <div>
            <dt>fresh</dt>
            <dd>{result.score.freshness.toFixed(2)}</dd>
          </div>
          <div>
            <dt>outcome</dt>
            <dd>{result.score.outcome.toFixed(2)}</dd>
          </div>
        </dl>
      ) : null}
      {result.provenance?.length ? (
        <p className="provenance-line">
          provenance · {result.provenance.join(" → ")}
        </p>
      ) : null}
      {recall.payload.results.length > 1 ? (
        <div className="recall-candidate-list" aria-label="Additional recalled evidence">
          {recall.payload.results.slice(1).map((candidate) => (
            <div key={candidate.memoryId}>
              <span>{candidate.memoryKind ?? "memory"}</span>
              <strong>{candidate.summary}</strong>
              <code>
                {(candidate.score?.composite ?? candidate.similarity).toFixed(2)}
              </code>
            </div>
          ))}
        </div>
      ) : null}
      {recall.payload.rejectedCount ? (
        <p className="gate-summary">
          {recall.payload.rejectedCount} unsafe, stale, or out-of-scope candidates rejected
        </p>
      ) : null}
    </article>
  );
}

function RecordedMemoryCard({ record }: { record: RecordEvent }) {
  return (
    <article className="memory-card memory-card--written">
      <div className="memory-card__eyebrow">
        <span className="written-label">
          <span aria-hidden="true">●</span> wrote {record.payload.memoryKind}
        </span>
        <span>{record.payload.freshnessMs}ms ago</span>
      </div>
      <strong className="memory-id">{record.payload.memoryId}</strong>
      <p>{record.payload.summary}</p>
      {record.payload.recalledBy ? (
        <div className="freshness-proof">
          <span aria-hidden="true">✓</span>
          <span>
            recalled by <strong>{record.payload.recalledBy.id}</strong>
            <small>
              @{record.payload.recalledBy.region} · {record.payload.staleReadsObserved} stale
              reads
            </small>
          </span>
        </div>
      ) : null}
    </article>
  );
}

function formatLearnedDate(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  }).format(date);
}

function MemoryTimeline({
  recall,
  record,
  evaluation,
}: {
  recall?: RecallEvent;
  record?: RecordEvent;
  evaluation?: EvaluationEvent;
}) {
  // The known-fact note is a real recalled semantic memory, not a static string.
  const semanticFact = recall?.payload.results.find(
    (result) => result.memoryKind === "semantic",
  );

  return (
    <aside className="rail memory-rail" aria-labelledby="memory-heading">
      <div className="rail-heading">
        <span>
          <span className="eyebrow">CockroachDB memory</span>
          <h2 id="memory-heading">Memory timeline</h2>
        </span>
        <span className="timeline-live">
          <span aria-hidden="true" /> live
        </span>
      </div>

      <div className="memory-timeline" aria-live="polite">
        <span className="timeline-axis" aria-hidden="true" />
        {record ? <RecordedMemoryCard record={record} /> : null}
        {recall ? <RecallEvidenceCard recall={recall} /> : null}
        {evaluation ? <MemoryLiftCard evaluation={evaluation} /> : null}
        {!recall ? (
          <div className="memory-empty">
            <span className="memory-empty__glyph" aria-hidden="true">
              ◇
            </span>
            <strong>Awaiting recall</strong>
            <p>The strongest prior case will attach here as evidence.</p>
          </div>
        ) : null}
        {semanticFact ? (
          <div className="timeline-note">
            <span className="timeline-note__marker" aria-hidden="true">
              ◇
            </span>
            <div>
              <strong>Semantic fact</strong>
              <p>{semanticFact.summary}</p>
              <small>current · learned {formatLearnedDate(semanticFact.learnedAt)}</small>
            </div>
          </div>
        ) : null}
      </div>

      <div className="memory-legend" aria-label="Memory timeline legend">
        <span>
          <i className="legend-shape legend-shape--recall" /> recalled
        </span>
        <span>
          <i className="legend-shape legend-shape--action" /> written
        </span>
        <span>
          <i className="legend-shape legend-shape--muted" /> known fact
        </span>
      </div>
    </aside>
  );
}

function MemoryLiftCard({ evaluation }: { evaluation: EvaluationEvent }) {
  const { retrieval, decisionQualityMeasured } = evaluation.payload;

  return (
    <article className="memory-lift-card" aria-label="Retrieval evaluation">
      <div className="memory-card__eyebrow">
        <span className="written-label">Phase 2 · retrieval</span>
        <span>{retrieval.hardNegativeCount} hard negatives</span>
      </div>
      <div className="arm-comparison">
        <span>
          recall@1 <strong>{retrieval.recallAt1.toFixed(2)}</strong>
        </span>
        <span>
          recall@10 <strong>{retrieval.recallAt10.toFixed(2)}</strong>
        </span>
        <span>
          nDCG@10 <strong>{retrieval.ndcgAt10.toFixed(2)}</strong>
        </span>
      </div>
      <div className="lift-number lift-number--pending">
        {decisionQualityMeasured ? (
          <strong>measured</strong>
        ) : (
          <span>
            MTTR / decision-quality: <strong>pending</strong> — not yet measured,
            requires the real reasoning agent
          </span>
        )}
      </div>
      <p className="provenance-line">
        real retrieval over a corpus with hard negatives · recall@1 &lt; 1.0 by design ·
        no simulated decision-quality numbers are shown
      </p>
    </article>
  );
}

export function IncidentConsole() {
  const { events, status, replay, pause } = useConsoleEvents();
  const view = useMemo(
    () => ({
      incident: eventOfType(events, "incident"),
      recall: eventOfType(events, "recall"),
      reason: eventOfType(events, "reason"),
      action: eventOfType(events, "act"),
      transaction: eventOfType(events, "transaction"),
      record: eventOfType(events, "record"),
      evaluation: eventOfType(events, "evaluation"),
      failover: eventOfType(events, "failover"),
    }),
    [events],
  );

  // Regions actually observed in the stream (agent identities + cross-region
  // recall). Real data — never a fixed three-region list.
  const regions = useMemo(() => {
    const seen = new Set<Region>();
    for (const event of events) {
      seen.add(event.agent.region);
      if (event.type === "record" && event.payload.recalledBy) {
        seen.add(event.payload.recalledBy.region);
      }
    }
    return [...seen];
  }, [events]);

  // Investigation elapsed = span from the incident alert to the latest event in
  // the stream. `null` (renders "—") until the incident arrives.
  const elapsedSeconds = useMemo(() => {
    const incident = view.incident;
    if (!incident) return null;
    const start = new Date(incident.occurredAt).getTime();
    if (!Number.isFinite(start)) return null;
    const latest = events.reduce((max, event) => {
      const at = new Date(event.occurredAt).getTime();
      return Number.isFinite(at) && at > max ? at : max;
    }, start);
    return Math.max(0, (latest - start) / 1000);
  }, [events, view.incident]);

  return (
    <div className="console-shell">
      <a className="skip-link" href="#investigation">
        Skip to investigation
      </a>
      <SystemStateBar
        streamStatus={status}
        incident={view.incident}
        failover={view.failover}
        regions={regions}
      />
      <div className="console-grid">
        <IncidentFeed incident={view.incident} recall={view.recall} />
        <Investigation
          incident={view.incident}
          reason={view.reason}
          action={view.action}
          transaction={view.transaction}
          hasRecall={Boolean(view.recall)}
          elapsedSeconds={elapsedSeconds}
        />
        <MemoryTimeline
          recall={view.recall}
          record={view.record}
          evaluation={view.evaluation}
        />
      </div>
      <footer className="console-footer">
        <span>
          <span className="footer-mark" aria-hidden="true">
            P
          </span>
          {view.incident?.caseId ?? "—"} · Phase 2 memory-lift proof
        </span>
        <span className="footer-controls">
          <button type="button" onClick={pause} disabled={status === "paused"}>
            Pause
          </button>
          <button type="button" onClick={replay}>
            Replay case
          </button>
        </span>
      </footer>
    </div>
  );
}
