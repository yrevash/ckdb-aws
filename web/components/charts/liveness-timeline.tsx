import { linearScale, stepAreaPath, stepPath, type Point } from "@/lib/chart";
import { ChartFrame } from "@/components/charts/chart-frame";

export type LivenessPhase = {
  id: string;
  label: string;
  /** live node count at this phase. */
  liveNodes: number;
  /** whether the killed region is down during this phase (marker colour). */
  down: boolean;
  /** small mono caption under the phase (e.g. "detected 4.6 s"). */
  sub?: string;
};

/**
 * Node-liveness over a region kill as a step/area timeline — the 9→6→9 money
 * shot with the outage band shaded in coral. Steps (not a smooth line) because
 * liveness holds then jumps. Single series; direct node-count labels at each
 * phase; the outage is a labelled band, never a mystery gap.
 */
export function LivenessTimeline({
  phases,
  expected,
  title,
  desc,
  caption,
}: {
  phases: LivenessPhase[];
  /** full/expected node count — sets the top of the y-scale and a reference line. */
  expected: number;
  title: string;
  desc?: string;
  caption?: string;
}) {
  const width = 560;
  const height = 240;
  const m = { top: 24, right: 24, bottom: 52, left: 40 };
  const plotW = width - m.left - m.right;
  const plotH = height - m.top - m.bottom;
  const baselineY = m.top + plotH;

  const yMax = expected;
  const yMin = Math.max(0, Math.min(...phases.map((p) => p.liveNodes)) - 1);
  const y = linearScale(yMin, yMax, baselineY, m.top);
  const stepW = plotW / phases.length;
  const cx = (i: number) => m.left + stepW * (i + 0.5);

  const points: Point[] = phases.map((p, i) => ({ x: cx(i), y: y(p.liveNodes) }));
  // Extend the step to the plot edges so the area spans the full width.
  const edged: Point[] = [
    { x: m.left, y: points[0].y },
    ...points,
    { x: m.left + plotW, y: points[points.length - 1].y },
  ];

  const downStart = phases.findIndex((p) => p.down);
  const downEnd = phases.length - 1 - [...phases].reverse().findIndex((p) => p.down);
  const hasBand = downStart !== -1;
  const bandX = hasBand ? m.left + stepW * downStart : 0;
  const bandW = hasBand ? stepW * (downEnd - downStart + 1) : 0;

  const yTicks = [yMin, Math.round((yMin + yMax) / 2), yMax].filter(
    (v, i, a) => a.indexOf(v) === i,
  );

  return (
    <ChartFrame
      title={title}
      desc={desc}
      width={width}
      height={height}
      caption={caption}
      table={phases.map((p) => [p.label, `${p.liveNodes}/${expected} nodes`])}
    >
      {/* outage band */}
      {hasBand ? (
        <>
          <rect className="live__band" x={bandX} y={m.top} width={bandW} height={plotH} />
          <line className="live__band-line" x1={bandX} y1={m.top} x2={bandX} y2={baselineY} />
          <line
            className="live__band-line"
            x1={bandX + bandW}
            y1={m.top}
            x2={bandX + bandW}
            y2={baselineY}
          />
        </>
      ) : null}

      {/* y grid + ticks */}
      <g className="chart__grid">
        {yTicks.map((t) => (
          <line key={t} x1={m.left} y1={y(t)} x2={m.left + plotW} y2={y(t)} />
        ))}
      </g>
      <g>
        {yTicks.map((t) => (
          <text
            key={t}
            className="chart__tick"
            x={m.left - 8}
            y={y(t)}
            textAnchor="end"
            dominantBaseline="central"
          >
            {t}
          </text>
        ))}
      </g>

      {/* area + step line */}
      <path className="live__area" d={stepAreaPath(edged, baselineY)} />
      <path className="live__line" d={stepPath(edged)} />

      {/* phase markers + labels */}
      {phases.map((p, i) => (
        <g key={p.id}>
          <circle
            className={`live__node${p.down ? " live__node--down" : ""}`}
            cx={cx(i)}
            cy={y(p.liveNodes)}
            r={5}
          />
          <text
            className="chart__value"
            x={cx(i)}
            y={y(p.liveNodes) - 14}
            textAnchor="middle"
          >
            {p.liveNodes}
          </text>
          <text
            className="live__phase-label"
            x={cx(i)}
            y={baselineY + 20}
            textAnchor="middle"
          >
            {p.label}
          </text>
          {p.sub ? (
            <text
              className="live__phase-sub"
              x={cx(i)}
              y={baselineY + 36}
              textAnchor="middle"
            >
              {p.sub}
            </text>
          ) : null}
        </g>
      ))}
    </ChartFrame>
  );
}
