import { clamp, linearScale } from "@/lib/chart";
import { ChartFrame } from "@/components/charts/chart-frame";

export type DriftTimelineFact = {
  memoryId: string;
  /** "superseded" (retired) vs "current" (valid now, valid_to === null). */
  status: "superseded" | "current";
  validFrom: string;
  validTo: string | null;
};

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** UTC "MMM D" — parsed deterministically so tick labels never shift by TZ. */
function fmtDate(epoch: number): string {
  const d = new Date(epoch);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

function epoch(value: string): number {
  return Date.parse(value);
}

/**
 * Bitemporal validity timeline — a fact's valid-time transition rendered as two
 * business-time bars: the superseded window (ghosted, dashed — no longer active)
 * and the currently-valid window (solid gold, open-ended to "now"). A dashed
 * marker sits at the migration instant the old fact was superseded, and a
 * checked marker sits on the current bar at the post-migration incident where
 * the agent had to choose the fix — proving it applied the currently-valid one,
 * not the stale one. Nothing is overwritten; both facts persist. Single entity
 * per row → direct labels, no legend (dataviz rule).
 */
export function MemoryDriftTimeline({
  facts,
  migrationAt,
  incidentAt,
  choseCurrent,
  title,
  desc,
  caption,
}: {
  facts: DriftTimelineFact[];
  /** business-time instant the old fact was superseded (current fact's valid_from). */
  migrationAt: string | null;
  /** the post-migration incident's decision time. */
  incidentAt: string | null;
  /** whether the agent applied the currently-valid fix at that incident. */
  choseCurrent: boolean;
  title: string;
  desc?: string;
  caption?: string;
}) {
  const width = 560;
  const barH = 20;
  const rowH = 56;
  const m = { top: 30, right: 20, bottom: 44, left: 152 };
  const plotW = width - m.left - m.right;
  const plotLeft = m.left;
  const plotRight = m.left + plotW;
  const height = m.top + facts.length * rowH + m.bottom;
  const plotBottom = m.top + facts.length * rowH;

  const froms = facts.map((f) => epoch(f.validFrom));
  const domainStart = Math.min(...froms);
  const migEpoch = migrationAt ? epoch(migrationAt) : Math.max(...froms);
  const incEpoch = incidentAt ? epoch(incidentAt) : migEpoch;
  const anchorEnd = Math.max(migEpoch, incEpoch, ...froms);
  const span = Math.max(anchorEnd - domainStart, 1);
  // Extend past the last real event so the open-ended current window reads.
  const domainEnd = domainStart + span * 1.4;
  const x = linearScale(domainStart, domainEnd, plotLeft, plotRight);

  const mx = x(migEpoch);
  const ix = x(incEpoch);
  const currentIndex = facts.findIndex((f) => f.status === "current");
  const incidentCy =
    m.top + (currentIndex < 0 ? 0 : currentIndex) * rowH + rowH / 2;
  const incidentLabelAnchor = ix > plotLeft + plotW * 0.55 ? "end" : "start";
  const incidentLabelX = incidentLabelAnchor === "end" ? ix - 14 : ix + 14;

  return (
    <ChartFrame
      title={title}
      desc={desc}
      width={width}
      height={height}
      caption={caption}
      table={facts.map((f) => [
        f.status === "current" ? "currently valid" : "superseded",
        `${fmtDate(epoch(f.validFrom))} – ${
          f.validTo ? fmtDate(epoch(f.validTo)) : "present"
        }`,
      ])}
    >
      {/* migration marker */}
      <line
        x1={mx}
        y1={m.top - 4}
        x2={mx}
        y2={plotBottom}
        style={{ stroke: "var(--memory)", strokeWidth: 1, strokeDasharray: "4 3" }}
      />
      <text
        className="chart__tick"
        x={mx}
        y={m.top - 12}
        textAnchor="middle"
        style={{ fill: "var(--memory)" }}
      >
        migration
      </text>

      {/* fact bars */}
      {facts.map((f, i) => {
        const cy = m.top + i * rowH + rowH / 2;
        const barY = cy - barH / 2;
        const x1 = x(epoch(f.validFrom));
        const x2 = f.validTo ? x(epoch(f.validTo)) : plotRight;
        const w = Math.max(6, clamp(x2 - x1, 0, plotW));
        const current = f.status === "current";
        return (
          <g key={f.memoryId}>
            <text
              className="live__phase-label"
              x={m.left - 14}
              y={cy - 6}
              textAnchor="end"
            >
              {current ? "currently valid" : "superseded"}
            </text>
            <text
              className="live__phase-sub"
              x={m.left - 14}
              y={cy + 9}
              textAnchor="end"
            >
              {f.memoryId}
            </text>
            <rect
              x={x1}
              y={barY}
              width={w}
              height={barH}
              rx={barH / 2}
              style={
                current
                  ? { fill: "var(--memory)" }
                  : {
                      fill: "var(--surface-2)",
                      stroke: "var(--line-strong)",
                      strokeDasharray: "4 3",
                    }
              }
            />
          </g>
        );
      })}

      {/* incident decision marker on the current window */}
      {incidentAt ? (
        <g>
          <circle
            cx={ix}
            cy={incidentCy}
            r={9}
            style={{
              fill: "var(--surface-1)",
              stroke: choseCurrent ? "var(--status-good)" : "var(--status-critical)",
              strokeWidth: 2,
            }}
          />
          {choseCurrent ? (
            <path
              d={`M ${ix - 3.4} ${incidentCy + 0.2} L ${ix - 0.9} ${
                incidentCy + 2.8
              } L ${ix + 3.8} ${incidentCy - 3}`}
              style={{
                fill: "none",
                stroke: "var(--status-good)",
                strokeWidth: 2,
                strokeLinecap: "round",
                strokeLinejoin: "round",
              }}
            />
          ) : (
            <path
              d={`M ${ix - 3} ${incidentCy - 3} L ${ix + 3} ${
                incidentCy + 3
              } M ${ix + 3} ${incidentCy - 3} L ${ix - 3} ${incidentCy + 3}`}
              style={{
                fill: "none",
                stroke: "var(--status-critical)",
                strokeWidth: 2,
                strokeLinecap: "round",
              }}
            />
          )}
          <text
            className="live__phase-sub"
            x={incidentLabelX}
            y={incidentCy + 3}
            textAnchor={incidentLabelAnchor}
            style={{ fill: "var(--ink-2)" }}
          >
            {choseCurrent ? "applied current fix" : "applied stale fix"}
          </text>
        </g>
      ) : null}

      {/* time axis */}
      <line
        className="chart__axis"
        x1={plotLeft}
        y1={plotBottom}
        x2={plotRight}
        y2={plotBottom}
      />
      {[
        { atX: plotLeft, label: fmtDate(domainStart) },
        { atX: mx, label: fmtDate(migEpoch) },
        { atX: plotRight, label: "now" },
      ].map((t) => (
        <text
          key={`${t.atX}-${t.label}`}
          className="chart__tick"
          x={clamp(t.atX, plotLeft, plotRight)}
          y={plotBottom + 18}
          textAnchor={
            t.atX <= plotLeft + 2 ? "start" : t.atX >= plotRight - 2 ? "end" : "middle"
          }
        >
          {t.label}
        </text>
      ))}
    </ChartFrame>
  );
}
