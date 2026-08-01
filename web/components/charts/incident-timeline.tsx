import type { CSSProperties } from "react";

import { ChartFrame } from "@/components/charts/chart-frame";

export type TimelineStage = {
  id: string;
  /** the station name (e.g. "Recall"). */
  label: string;
  /** a short mono sub-line under the label (e.g. "+3.2s"). */
  time?: string;
  /** a semantic colour token, e.g. "var(--memory)". */
  tone: string;
  /** whether this station has occurred (solid) vs pending (ghosted). */
  active?: boolean;
};

export type TimelineEnvelope = {
  fromId: string;
  toId: string;
  label: string;
};

/**
 * The incident response as a single left-to-right rail:
 * perceive → recall → reason → act → record. Each station is a coloured node
 * (colour follows the *kind* of step, not its rank); an optional bracket groups
 * the stations that commit inside one transaction. Purpose-built for the
 * Incident view — it tells the "memory changed the action" story at a glance,
 * not as a log. Text reuses the shared `.chart__*` classes; colour is applied
 * inline from design tokens only.
 */
export function IncidentTimeline({
  stages,
  envelope,
  title,
  desc,
  caption,
}: {
  stages: TimelineStage[];
  envelope?: TimelineEnvelope;
  title: string;
  desc?: string;
  caption?: string;
}) {
  const width = 580;
  const height = 138;
  const padX = 46;
  const railY = 78;
  const nodeR = 8;
  const n = stages.length;
  const step = n > 1 ? (width - padX * 2) / (n - 1) : 0;
  const xAt = (i: number) => (n > 1 ? padX + i * step : width / 2);

  const fromIdx = envelope ? stages.findIndex((s) => s.id === envelope.fromId) : -1;
  const toIdx = envelope ? stages.findIndex((s) => s.id === envelope.toId) : -1;
  const hasEnvelope = envelope !== undefined && fromIdx >= 0 && toIdx >= 0;

  const bracketY = 30;
  const bracketDrop = railY - nodeR - 8;

  return (
    <ChartFrame
      title={title}
      desc={desc}
      width={width}
      height={height}
      caption={caption}
      table={stages.map((s) => [s.label, s.time ?? "—"])}
    >
      {/* the rail every station sits on */}
      <line
        x1={xAt(0)}
        y1={railY}
        x2={xAt(n - 1)}
        y2={railY}
        style={{ stroke: "var(--line-strong)", strokeWidth: 2 }}
        strokeLinecap="round"
      />

      {/* one-transaction envelope bracket over the committing stations */}
      {hasEnvelope ? (
        <g>
          <path
            d={`M ${xAt(fromIdx)} ${bracketDrop} L ${xAt(fromIdx)} ${bracketY} L ${xAt(
              toIdx,
            )} ${bracketY} L ${xAt(toIdx)} ${bracketDrop}`}
            fill="none"
            style={{ stroke: "var(--accent-line)", strokeWidth: 1.5 }}
            strokeDasharray="3 3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <text
            className="chart__tick"
            x={(xAt(fromIdx) + xAt(toIdx)) / 2}
            y={bracketY - 6}
            textAnchor="middle"
            style={{ fill: "var(--accent)" }}
          >
            {envelope?.label}
          </text>
        </g>
      ) : null}

      {stages.map((s, i) => {
        const x = xAt(i);
        const dim: CSSProperties = s.active === false ? { opacity: 0.4 } : {};
        return (
          <g key={s.id} style={dim}>
            {s.active !== false ? (
              <circle cx={x} cy={railY} r={nodeR + 5} fill={s.tone} opacity={0.16} />
            ) : null}
            <circle cx={x} cy={railY} r={nodeR} fill={s.tone} />
            <circle cx={x} cy={railY} r={3} fill="var(--chart-surface)" />
            <text
              className="chart__label"
              x={x}
              y={railY + 26}
              textAnchor="middle"
              style={{ fill: "var(--ink-1)", fontWeight: 600 }}
            >
              {s.label}
            </text>
            {s.time ? (
              <text className="chart__tick" x={x} y={railY + 42} textAnchor="middle">
                {s.time}
              </text>
            ) : null}
          </g>
        );
      })}
    </ChartFrame>
  );
}
