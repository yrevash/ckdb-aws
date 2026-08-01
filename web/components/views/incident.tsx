"use client";

import type { CSSProperties, ReactNode } from "react";

import { useConsoleEvents, type StreamStatus } from "@/hooks/use-console-events";
import type { ConsoleEvent, RecallResult } from "@/lib/events";
import { ABSENT, formatDuration, formatMs, formatRatio } from "@/lib/format";
import {
  IncidentTimeline,
  type TimelineStage,
} from "@/components/charts/incident-timeline";
import { RankedBars, type RankedBar } from "@/components/charts";
import {
  Badge,
  Card,
  SectionHeader,
  SourceTag,
  StatusDot,
  type SourceKind,
  type Tone,
} from "@/components/ui";

/* ------------------------------------------------------------------ helpers */

/** live SSE → "measured"; the labelled replay fixture → "replay". */
function statusSource(status: StreamStatus): SourceKind {
  return status === "live" ? "measured" : "replay";
}

/** most-recent event of a given type, fully typed. */
function latest<T extends ConsoleEvent["type"]>(
  events: readonly ConsoleEvent[],
  type: T,
): Extract<ConsoleEvent, { type: T }> | undefined {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    if (event.type === type) return event as Extract<ConsoleEvent, { type: T }>;
  }
  return undefined;
}

function secondsBetween(iso?: string, startIso?: string): number | null {
  if (!iso || !startIso) return null;
  const a = Date.parse(iso);
  const b = Date.parse(startIso);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return (a - b) / 1000;
}

/** a stage's offset from the incident opening, e.g. "+3.2s". */
function formatOffset(seconds: number | null): string {
  if (seconds === null) return ABSENT;
  return `+${seconds.toFixed(1)}s`;
}

/** wall-clock elapsed, adaptively "4.4s" or "1m 11s". */
function formatElapsed(seconds: number | null): string {
  if (seconds === null) return ABSENT;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

/** p99 in ms → an adaptive "4.2 s" / "820 ms"; absent → em-dash. */
function formatLatency(ms?: number): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return ABSENT;
  const d = formatDuration(ms / 1000);
  return d.unit ? `${d.value} ${d.unit}` : d.value;
}

const SEVERITY_TONE: Record<string, Tone> = {
  "SEV-1": "kill",
  "SEV-2": "warn",
  "SEV-3": "neutral",
};

const ROLE_TONE: Record<string, Tone> = {
  action: "action",
  memory: "memory",
  audit: "neutral",
};

const KIND_TONE: Record<string, Tone> = {
  episodic: "action",
  semantic: "memory",
  procedural: "accent",
};

function delay(ms: number): CSSProperties {
  return { ["--reveal-delay" as string]: `${ms}ms` };
}

/* ------------------------------------------------------------- small pieces */

/** a compact labelled reading for the incident header (token-styled). */
function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "2px",
        padding: "var(--sp-3) var(--sp-4)",
        borderRadius: "var(--r-md)",
        border: "1px solid var(--line)",
        background: "var(--surface-1)",
        minWidth: "104px",
      }}
    >
      <span
        style={{
          fontSize: "var(--fs-micro)",
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          color: "var(--ink-3)",
        }}
      >
        {label}
      </span>
      <span
        className="mono"
        style={{ fontSize: "var(--fs-h3)", fontWeight: 600, color: "var(--ink-1)" }}
      >
        {value}
      </span>
    </div>
  );
}

/** a labelled readout used inside cards for record / cross-agent facts. */
function Readout({
  label,
  value,
  hint,
  accent = "var(--accent)",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  accent?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--sp-1)",
        paddingLeft: "var(--sp-3)",
        borderLeft: `2px solid ${accent}`,
      }}
    >
      <span style={{ fontSize: "var(--fs-small)", fontWeight: 600, color: "var(--ink-2)" }}>
        {label}
      </span>
      <span
        className="mono"
        style={{ fontSize: "var(--fs-h2)", fontWeight: 600, color: "var(--ink-1)", lineHeight: 1 }}
      >
        {value}
      </span>
      {hint ? (
        <span style={{ fontSize: "var(--fs-small)", color: "var(--ink-3)" }}>{hint}</span>
      ) : null}
    </div>
  );
}

/* --------------------------------------------------------------- empty view */

function IncidentEmpty({ source }: { source: SourceKind }) {
  return (
    <div>
      <header className="ov-hero reveal">
        <span className="ov-hero__eyebrow">Source A · Live incident stream</span>
        <h1 className="ov-hero__title">
          Waiting for an <em>incident.</em>
        </h1>
        <p className="ov-hero__lede">
          The perceive → recall → reason → act → record story renders here the moment the
          stream opens. Nothing is drawn until a real event arrives — no placeholder numbers.
        </p>
        <div className="ov-hero__meta">
          <span className="ui-source ui-source--measured">
            <StatusDot tone="neutral" pulse />
            Listening on the console stream
          </span>
          <SourceTag kind={source} detail="events.ts" />
        </div>
      </header>
    </div>
  );
}

/* -------------------------------------------------------------- the real view */

/**
 * Pure, testable presentation of one incident's investigation. Takes the raw
 * console event list + its provenance and renders the story; every figure is
 * traced to a real event field and shows `—` when the producing system didn't
 * measure it (Reality Charter R6).
 */
export function IncidentView({
  events,
  source,
}: {
  events: readonly ConsoleEvent[];
  source: SourceKind;
}) {
  const incident = latest(events, "incident");
  if (!incident) return <IncidentEmpty source={source} />;

  // Scope every other panel to this incident's case so a multi-case live
  // stream never mixes stories.
  const caseId = incident.caseId;
  const scoped = events.filter((e) => e.caseId === caseId);

  const recall = latest(scoped, "recall");
  const reason = latest(scoped, "reason");
  const act = latest(scoped, "act");
  const transaction = latest(scoped, "transaction");
  const record = latest(scoped, "record");

  const start = incident.occurredAt;
  const lastAt = scoped.reduce<string>((acc, e) => (e.occurredAt > acc ? e.occurredAt : acc), start);
  const elapsed = formatElapsed(secondsBetween(lastAt, start));

  const tel = incident.payload.telemetry;
  const sevTone = SEVERITY_TONE[incident.payload.severity] ?? "neutral";

  // ---- recall candidates + the winning prior case -------------------------
  const results = recall?.payload.results ?? [];
  const winnerId = act?.payload.citedMemoryId ?? reason?.payload.citedMemoryIds[0];
  const byScore = [...results].sort(
    (a, b) => (b.score?.composite ?? b.similarity) - (a.score?.composite ?? a.similarity),
  );
  const winner: RecallResult | undefined =
    results.find((r) => r.memoryId === winnerId) ?? byScore[0];

  const candidateBars: RankedBar[] = [...results]
    .sort((a, b) => b.similarity - a.similarity)
    .map((r) => ({
      label: r.memoryId,
      value: r.similarity,
      display: formatRatio(r.similarity),
      emphasis: r.memoryId === winner?.memoryId,
    }));

  const scoreBars: RankedBar[] = winner?.score
    ? (
        [
          ["vector", winner.score.vector],
          ["scope", winner.score.scope],
          ["freshness", winner.score.freshness],
          ["outcome", winner.score.outcome],
          ["composite", winner.score.composite],
        ] as const
      ).map(([label, value]) => ({
        label,
        value,
        display: formatRatio(value),
        emphasis: label === "composite",
      }))
    : [];

  // ---- the response as a rail --------------------------------------------
  const stageDefs: {
    id: string;
    label: string;
    tone: string;
    at?: string;
    present: boolean;
  }[] = [
    { id: "perceive", label: "Perceive", tone: "var(--kill)", at: incident.occurredAt, present: true },
    { id: "recall", label: "Recall", tone: "var(--memory)", at: recall?.occurredAt, present: !!recall },
    { id: "reason", label: "Reason", tone: "var(--accent)", at: reason?.occurredAt, present: !!reason },
    { id: "act", label: "Act", tone: "var(--action)", at: act?.occurredAt, present: !!act },
    { id: "record", label: "Record", tone: "var(--memory)", at: record?.occurredAt, present: !!record },
  ];
  const stages: TimelineStage[] = stageDefs.map((s) => ({
    id: s.id,
    label: s.label,
    tone: s.tone,
    active: s.present,
    time: s.present ? formatOffset(secondsBetween(s.at, start)) : undefined,
  }));
  const envelope =
    act && record
      ? { fromId: "act", toId: "record", label: "one transaction" }
      : undefined;

  // ---- act, framed as a human sentence ------------------------------------
  const actArgs = act?.payload.arguments;
  const actSentence =
    act && actArgs
      ? `${String(actArgs.service ?? act.payload.target)} · ${
          actArgs.fromDeploy !== undefined ? `#${actArgs.fromDeploy}` : ABSENT
        } → ${actArgs.toDeploy !== undefined ? `#${actArgs.toDeploy}` : ABSENT}`
      : ABSENT;

  return (
    <div>
      {/* ---------------------------------------------------------- header */}
      <header className="ov-hero reveal">
        <span className="ov-hero__eyebrow">Source A · Live incident stream</span>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)", flexWrap: "wrap" }}>
          <Badge tone={sevTone}>{incident.payload.severity}</Badge>
          <span className="mono" style={{ color: "var(--ink-2)", fontSize: "var(--fs-small)" }}>
            {incident.payload.service}
          </span>
          <span style={{ color: "var(--ink-3)" }}>·</span>
          <span className="mono" style={{ color: "var(--ink-3)", fontSize: "var(--fs-small)" }}>
            {caseId}
          </span>
          <span
            className="ui-source ui-source--measured"
            style={{ marginLeft: "var(--sp-2)" }}
          >
            <StatusDot tone={incident.payload.status === "open" ? "critical" : "good"} pulse={incident.payload.status === "open"} />
            {incident.payload.status}
          </span>
        </div>
        <h1 className="ov-hero__title">
          Memory <em>changed the action.</em>
        </h1>
        <p className="ov-hero__lede">{incident.payload.summary}</p>

        <div className="ov-hero__meta">
          <Metric label="Severity" value={incident.payload.severity} />
          <Metric label="Service" value={incident.payload.service} />
          <Metric label="Elapsed" value={elapsed} />
          <Metric label="p99 latency" value={formatLatency(tel?.p99LatencyMs)} />
          <Metric
            label="Error rate"
            value={typeof tel?.errorRatePct === "number" ? `${tel.errorRatePct}%` : ABSENT}
          />
          <Metric label="Deploy" value={tel?.deploy ?? ABSENT} />
          <Metric
            label="Budget burn"
            value={
              typeof tel?.errorBudgetBurnRate === "number" ? `${tel.errorBudgetBurnRate}×` : ABSENT
            }
          />
        </div>
        <div className="ov-hero__meta">
          <SourceTag kind={source} detail="events.ts" />
        </div>
      </header>

      {/* ---------------------------------------------------- the response rail */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="The response"
          title="Perceive → Recall → Reason → Act → Record"
          description="Five steps, one story. The last two commit inside a single transaction, so the fix and the memory of it either both land or neither does."
          meta={<SourceTag kind={source} detail="events.ts" />}
        />
        <Card
          className="reveal"
          title="Incident response timeline"
          aside={<Badge tone="action">{caseId}</Badge>}
        >
          <IncidentTimeline
            stages={stages}
            envelope={envelope}
            title="Incident response timeline"
            desc="Perceive, recall, reason, act, then record — the act and record steps grouped inside one transaction."
            caption="Colour marks the kind of step, not its rank. The bracket is the atomic action + memory commit."
          />
        </Card>
      </section>

      {/* --------------------------------------------------- why this action */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Why this action"
          title="A prior case decided the fix"
          description="The agent recalled similar incidents, ranked them under hard negatives, and adapted the winning case's remediation — it did not copy a template."
          meta={<Badge tone="memory">memory</Badge>}
        />

        {reason ? (
          <figure
            className="reveal"
            style={{
              margin: 0,
              marginBottom: "var(--sp-4)",
              padding: "var(--sp-5)",
              borderRadius: "var(--r-lg)",
              border: "1px solid var(--line)",
              borderLeft: "3px solid var(--memory)",
              background: "var(--surface-1)",
              boxShadow: "var(--shadow-1)",
            }}
          >
            <p style={{ fontSize: "var(--fs-h3)", color: "var(--ink-1)", maxWidth: "72ch" }}>
              {reason.payload.message}
            </p>
            <figcaption
              style={{
                display: "flex",
                gap: "var(--sp-2)",
                alignItems: "center",
                flexWrap: "wrap",
                marginTop: "var(--sp-3)",
              }}
            >
              <span style={{ fontSize: "var(--fs-small)", color: "var(--ink-3)" }}>cites</span>
              {reason.payload.citedMemoryIds.map((id) => (
                <Badge key={id} tone="memory">
                  {id}
                </Badge>
              ))}
              {reason.payload.citedRunbookIds.map((id) => (
                <Badge key={id} tone="neutral">
                  {id}
                </Badge>
              ))}
            </figcaption>
          </figure>
        ) : null}

        <div className="ov-split reveal" style={delay(60)}>
          <Card
            title="Recall candidates"
            aside={
              recall ? (
                <span className="mono" style={{ fontSize: "var(--fs-micro)", color: "var(--ink-3)" }}>
                  {formatMs(recall.payload.durationMs)}
                  {typeof recall.payload.rejectedCount === "number"
                    ? ` · ${recall.payload.rejectedCount} rejected`
                    : ""}
                </span>
              ) : (
                <SourceTag kind={source} detail="recall" />
              )
            }
          >
            {candidateBars.length > 0 ? (
              <RankedBars
                data={candidateBars}
                max={1}
                title="Recall candidates by similarity"
                desc={`${candidateBars.length} prior memories ranked by similarity; the cited winner is emphasised.`}
                caption={
                  winner
                    ? `Winner ${winner.memoryId} — the ${winner.similarity.toFixed(2)} match the agent cited.`
                    : undefined
                }
              />
            ) : (
              <p className="ui-stat__hint">{ABSENT} no recall results on the stream yet.</p>
            )}
          </Card>

          <Card
            title="Winning prior case"
            aside={
              winner?.memoryKind ? (
                <Badge tone={KIND_TONE[winner.memoryKind] ?? "neutral"}>{winner.memoryKind}</Badge>
              ) : undefined
            }
          >
            {winner ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-1)" }}>
                  <span className="mono" style={{ fontSize: "var(--fs-h3)", color: "var(--ink-1)", fontWeight: 600 }}>
                    {winner.sourceCaseId}
                  </span>
                  <span style={{ color: "var(--ink-2)", fontSize: "var(--fs-small)" }}>
                    {winner.summary}
                  </span>
                </div>

                {winner.successfulAction ? (
                  <Readout
                    label="Prior fix that worked"
                    value={
                      <span style={{ fontSize: "var(--fs-body)", fontWeight: 500 }}>
                        {winner.successfulAction}
                      </span>
                    }
                    accent="var(--memory)"
                    hint={
                      typeof winner.successRate === "number"
                        ? `success rate ${formatRatio(winner.successRate)} across prior applications`
                        : undefined
                    }
                  />
                ) : null}

                {scoreBars.length > 0 ? (
                  <RankedBars
                    data={scoreBars}
                    max={1}
                    title="Winning case — component scores"
                    desc="Vector, scope, freshness and outcome components blended into the composite score."
                    caption="Composite (emphasised) blends similarity with scope, freshness and prior outcome."
                  />
                ) : (
                  <p className="ui-stat__hint">{ABSENT} no component scores recorded.</p>
                )}
              </div>
            ) : (
              <p className="ui-stat__hint">{ABSENT} no winning case selected yet.</p>
            )}
          </Card>
        </div>
      </section>

      {/* --------------------------------------------- the one-transaction envelope */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="The commit"
          title="Action and memory, one transaction"
          description={
            act
              ? `Proposed: ${act.payload.tool} — ${actSentence}${
                  act.payload.requiresApproval ? " (needs approval)" : ""
                }. It applies as a single atomic write.`
              : "The remediation and its memory commit atomically — both apply or neither does."
          }
          meta={<Badge tone="action">action</Badge>}
        />

        <div className="ov-split reveal" style={delay(60)}>
          <Card
            title="Transaction envelope"
            aside={
              transaction ? (
                <StatusDot
                  tone={transaction.payload.state === "committed" ? "good" : "warn"}
                  label={transaction.payload.state}
                />
              ) : (
                <SourceTag kind={source} detail="transaction" />
              )
            }
          >
            {transaction ? (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--sp-3)",
                  borderLeft: "2px solid var(--accent-line)",
                  paddingLeft: "var(--sp-4)",
                }}
              >
                <div className="mono" style={{ fontSize: "var(--fs-small)", color: "var(--ink-3)" }}>
                  BEGIN
                  <span style={{ color: "var(--ink-3)" }}> · txn {transaction.payload.transactionId}</span>
                </div>

                {transaction.payload.statements.map((stmt, i) => (
                  <div
                    key={`${stmt.target}-${i}`}
                    style={{ display: "flex", gap: "var(--sp-3)", alignItems: "flex-start" }}
                  >
                    <span
                      className="mono"
                      style={{
                        fontSize: "var(--fs-micro)",
                        fontWeight: 600,
                        color: "var(--ink-2)",
                        border: "1px solid var(--line)",
                        borderRadius: "var(--r-sm)",
                        padding: "2px 6px",
                        background: "var(--surface-inset)",
                        flex: "none",
                        minWidth: "58px",
                        textAlign: "center",
                      }}
                    >
                      {stmt.operation}
                    </span>
                    <div style={{ display: "flex", flexDirection: "column", gap: "2px", minWidth: 0 }}>
                      <div style={{ display: "flex", gap: "var(--sp-2)", alignItems: "center", flexWrap: "wrap" }}>
                        <span className="mono" style={{ color: "var(--ink-1)", fontWeight: 600 }}>
                          {stmt.target}
                        </span>
                        <Badge tone={ROLE_TONE[stmt.role] ?? "neutral"}>{stmt.role}</Badge>
                      </div>
                      <span style={{ fontSize: "var(--fs-small)", color: "var(--ink-2)" }}>
                        {stmt.summary}
                      </span>
                    </div>
                  </div>
                ))}

                <div className="mono" style={{ fontSize: "var(--fs-small)", color: "var(--ink-1)", fontWeight: 600 }}>
                  COMMIT
                  <span style={{ color: "var(--ink-3)", fontWeight: 400 }}>
                    {" · "}
                    {formatOffset(secondsBetween(transaction.payload.committedAt, start))} · {transaction.payload.state}
                  </span>
                </div>
              </div>
            ) : (
              <p className="ui-stat__hint">{ABSENT} no transaction on the stream yet.</p>
            )}
            {transaction ? (
              <p className="chart-caption" style={{ marginTop: "var(--sp-4)" }}>
                The rollback and its episodic memory are the same commit — atomic by construction.
              </p>
            ) : null}
          </Card>

          <Card
            title="Recorded to memory"
            aside={record ? <Badge tone={KIND_TONE[record.payload.memoryKind] ?? "neutral"}>{record.payload.memoryKind}</Badge> : undefined}
          >
            {record ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-5)" }}>
                <Readout
                  label="Write → read freshness"
                  value={formatMs(record.payload.freshnessMs)}
                  accent="var(--action)"
                  hint={
                    record.payload.recalledBy
                      ? `re-read by ${record.payload.recalledBy.id} in ${record.payload.recalledBy.region}`
                      : "read-your-writes on the same region"
                  }
                />
                <Readout
                  label="Stale reads observed"
                  value={String(record.payload.staleReadsObserved)}
                  accent={record.payload.staleReadsObserved === 0 ? "var(--status-good)" : "var(--status-warn)"}
                  hint={`new memory ${record.payload.memoryId} · ${record.payload.summary}`}
                />
                <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)" }}>
                  <SourceTag kind={source} detail="record" />
                </div>
              </div>
            ) : (
              <p className="ui-stat__hint">{ABSENT} nothing recorded yet.</p>
            )}
          </Card>
        </div>
      </section>
    </div>
  );
}

/* ------------------------------------------------------- live-wired default */

/**
 * The default export wires the honest event stream (`useConsoleEvents`) into the
 * pure `IncidentView`. Live SSE reads as "measured"; the labelled replay fixture
 * reads as "replay" — every figure carries that provenance.
 */
export function Incident() {
  const { events, status } = useConsoleEvents();
  return <IncidentView events={events} source={statusSource(status)} />;
}
