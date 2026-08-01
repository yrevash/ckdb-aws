import { clamp, linearScale } from "@/lib/chart";
import { ChartFrame } from "@/components/charts/chart-frame";

export type RankedBar = {
  label: string;
  value: number;
  /** printed at the bar end; falls back to the raw value. */
  display?: string;
  /** paint this bar with the brand accent (e.g. the headline metric). */
  emphasis?: boolean;
};

/**
 * Horizontal ranked bar/series chart — magnitude across a few named rows
 * (recall@k, leaseholders per region, similarity of recall candidates). Single
 * hue by default (identity is the row label, not the color); direct value
 * labels at each bar end, no legend, recessive track. Rows render top-to-bottom
 * in the order given — sort before passing if rank matters.
 */
export function RankedBars({
  data,
  max = 1,
  title,
  desc,
  caption,
  rowHeight = 34,
  barHeight = 12,
  labelWidth = 128,
  valueWidth = 52,
}: {
  data: RankedBar[];
  max?: number;
  title: string;
  desc?: string;
  caption?: string;
  rowHeight?: number;
  barHeight?: number;
  labelWidth?: number;
  valueWidth?: number;
}) {
  const width = 520;
  const padY = 6;
  const height = data.length * rowHeight + padY * 2;
  const trackX = labelWidth;
  const trackW = width - labelWidth - valueWidth;
  const x = linearScale(0, max, 0, trackW);

  return (
    <ChartFrame
      title={title}
      desc={desc}
      width={width}
      height={height}
      caption={caption}
      table={data.map((d) => [d.label, d.display ?? String(d.value)])}
    >
      {data.map((d, i) => {
        const cy = padY + i * rowHeight + rowHeight / 2;
        const barY = cy - barHeight / 2;
        const w = clamp(x(clamp(d.value, 0, max)), 0, trackW);
        return (
          <g className="rbar" key={d.label}>
            <text
              className="rbar__row-label"
              x={labelWidth - 12}
              y={cy}
              textAnchor="end"
              dominantBaseline="central"
            >
              {d.label}
            </text>
            <rect
              className="rbar__track"
              x={trackX}
              y={barY}
              width={trackW}
              height={barHeight}
              rx={barHeight / 2}
            />
            <rect
              className={`rbar__fill${d.emphasis ? " rbar__fill--accent" : ""}`}
              x={trackX}
              y={barY}
              width={Math.max(barHeight, w)}
              height={barHeight}
              rx={barHeight / 2}
            />
            <text
              className="chart__value"
              x={width - valueWidth + 8}
              y={cy}
              dominantBaseline="central"
            >
              {d.display ?? String(d.value)}
            </text>
          </g>
        );
      })}
    </ChartFrame>
  );
}
