"use client";

import { useMemo, useState } from "react";

import { useConsoleEvents } from "@/hooks/use-console-events";
import type {
  ActEvent,
  ConsoleEvent,
  EvaluationEvent,
  IncidentEvent,
  RecallEvent,
  ReasonEvent,
  RecordEvent,
  TransactionEvent,
} from "@/lib/events";

const historicalCases = [
  {
    id: "CASE-2041",
    service: "checkout-api",
    meta: "SEV-1 · now",
    status: "active",
    region: "us-east",
  },
  {
    id: "CASE-2038",
    service: "billing-worker",
    meta: "resolved · 23m",
    status: "resolved",
    region: "eu-west",
  },
  {
    id: "CASE-1878",
    service: "checkout-api",
    meta: "resolved · 14 Mar",
    status: "recalled",
    region: "us-west",
  },
  {
    id: "CASE-2035",
    service: "search-api",
    meta: "resolved · 41m",
    status: "resolved",
    region: "us-east",
  },
] as const;

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
}: {
  streamStatus: "connecting" | "live" | "replay" | "paused";
}) {
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
        <strong>CASE-2041</strong>
        <span className="case-service">checkout-api</span>
        <span className="severity-badge">
          <span aria-hidden="true">◆</span> SEV-1
        </span>
      </div>

      <div className="system-state" aria-label="CockroachDB system state">
        <div className="regions" aria-label="Three healthy database regions">
          {(["us-east", "us-west", "eu-west"] as const).map((region) => (
            <span className="region" key={region}>
              <span className="region__dot" aria-hidden="true" />
              {region}
            </span>
          ))}
        </div>
        <div className="thesis-metrics">
          <span className="metric">
            <span className="metric__label">RPO</span>
            <strong>0</strong>
            <span className="metric__unit">rows</span>
          </span>
          <span className="metric">
            <span className="metric__label">RTO</span>
            <strong>—</strong>
          </span>
          <span className="cluster-health">
            <span aria-hidden="true">●</span> healthy
          </span>
        </div>
      </div>

      <StreamIndicator status={streamStatus} />
    </header>
  );
}

function IncidentFeed() {
  const [filter, setFilter] = useState<"all" | "sev1" | "checkout">("all");

  const visibleCases = historicalCases.filter((item) => {
    if (filter === "sev1") return item.meta.includes("SEV-1");
    if (filter === "checkout") return item.service === "checkout-api";
    return true;
  });

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
        {visibleCases.map((incident) => (
          <button
            className={`case-row case-row--${incident.status}`}
            type="button"
            key={incident.id}
            aria-current={incident.status === "active" ? "true" : undefined}
          >
            <span className="case-row__rail" aria-hidden="true" />
            <span className="case-row__topline">
              <strong>{incident.id}</strong>
              <span className="case-row__state">
                {incident.status === "active"
                  ? "active"
                  : incident.status === "recalled"
                    ? "memory source"
                    : "closed"}
              </span>
            </span>
            <span className="case-row__service">{incident.service}</span>
            <span className="case-row__meta">
              <span>{incident.meta}</span>
              <span>{incident.region}</span>
            </span>
          </button>
        ))}
      </div>

      <fieldset className="filters">
        <legend className="eyebrow">Filter cases</legend>
        <div className="filter-options">
          {(
            [
              ["all", "All"],
              ["sev1", "SEV-1"],
              ["checkout", "Checkout"],
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

      <div className="rail-footnote">
        <Glyph tone="recall">◇</Glyph>
        <span>
          <strong>CASE-1878</strong> is visible before recall—the prior case is evidence,
          not hidden context.
        </span>
      </div>
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
}: {
  incident?: IncidentEvent;
  reason?: ReasonEvent;
  action?: ActEvent;
  transaction?: TransactionEvent;
  hasRecall: boolean;
}) {
  const [approved, setApproved] = useState(false);

  return (
    <main className="investigation" id="investigation" aria-labelledby="investigation-heading">
      <div className="investigation__header">
        <div>
          <span className="eyebrow">CASE-2041 · live investigation</span>
          <h1 id="investigation-heading">Checkout latency after canary</h1>
        </div>
        <span className="elapsed">
          <span aria-hidden="true">◷</span> 04:18 elapsed
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
                  p99 <strong>4.2s</strong>
                </span>
                <span>
                  errors <strong>18.4%</strong>
                </span>
                <span>
                  deploy <strong>#5120</strong>
                </span>
                <span>
                  burn <strong>16.2×</strong>
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
                service=&quot;checkout-api&quot;, to=&quot;#5119&quot;
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

function MemoryTimeline({
  recall,
  record,
  evaluation,
}: {
  recall?: RecallEvent;
  record?: RecordEvent;
  evaluation?: EvaluationEvent;
}) {
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
        <div className="timeline-note">
          <span className="timeline-note__marker" aria-hidden="true">
            ◇
          </span>
          <div>
            <strong>Semantic fact</strong>
            <p>checkout-api deploy policy: rollback requires approval</p>
            <small>current · learned 12 Jun</small>
          </div>
        </div>
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
  const { cold, memory } = evaluation.payload;
  const improvement = Math.round(
    ((cold.medianMttrSeconds - memory.medianMttrSeconds) /
      cold.medianMttrSeconds) *
      100,
  );
  const maximum = Math.max(
    ...evaluation.payload.learningCurve.flatMap((point) => [
      point.coldMttrSeconds,
      point.memoryMttrSeconds,
    ]),
  );

  return (
    <article className="memory-lift-card" aria-label="Memory versus cold evaluation">
      <div className="memory-card__eyebrow">
        <span className="written-label">Phase 2 proof</span>
        <span>{evaluation.payload.familyCount} families</span>
      </div>
      <div className="lift-number">
        <strong>{improvement}%</strong>
        <span>lower median MTTR with memory</span>
      </div>
      <div className="arm-comparison">
        <span>
          cold <strong>{cold.medianMttrSeconds}s</strong>
        </span>
        <span>
          memory <strong>{memory.medianMttrSeconds}s</strong>
        </span>
        <span>
          recall@10 <strong>{evaluation.payload.recallAt10.toFixed(2)}</strong>
        </span>
      </div>
      <div className="learning-curve" aria-label="MTTR by recurrence">
        {evaluation.payload.learningCurve.map((point) => (
          <div className="curve-pair" key={point.occurrence}>
            <span
              className="curve-bar curve-bar--cold"
              style={{ height: `${(point.coldMttrSeconds / maximum) * 100}%` }}
              title={`Cold occurrence ${point.occurrence}: ${point.coldMttrSeconds}s`}
            />
            <span
              className="curve-bar curve-bar--memory"
              style={{ height: `${(point.memoryMttrSeconds / maximum) * 100}%` }}
              title={`Memory occurrence ${point.occurrence}: ${point.memoryMttrSeconds}s`}
            />
            <small>#{point.occurrence}</small>
          </div>
        ))}
      </div>
      <p className="provenance-line">
        same seed · fewer wrong actions {cold.wrongActions} → {memory.wrongActions} · failed
        orders {cold.failedOrders} → {memory.failedOrders}
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
    }),
    [events],
  );

  return (
    <div className="console-shell">
      <a className="skip-link" href="#investigation">
        Skip to investigation
      </a>
      <SystemStateBar streamStatus={status} />
      <div className="console-grid">
        <IncidentFeed />
        <Investigation
          incident={view.incident}
          reason={view.reason}
          action={view.action}
          transaction={view.transaction}
          hasRecall={Boolean(view.recall)}
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
          CASE-2041 · Phase 2 memory-lift proof
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
