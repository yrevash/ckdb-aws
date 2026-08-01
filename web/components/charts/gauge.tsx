import { clamp01, describeArc } from "@/lib/chart";
import { ChartFrame } from "@/components/charts/chart-frame";

/**
 * A radial gauge for a single 0..1 quality score (nDCG@10, temporal-validity
 * accuracy). A 270° open dial: recessive track + one accent value arc, the
 * figure read large in the middle. Single value → no legend; the title names it.
 */
export function Gauge({
  value,
  title,
  desc,
  centerLabel,
  centerCaption,
  size = 200,
  stroke = 14,
}: {
  value: number;
  title: string;
  desc?: string;
  /** the big printed figure, already formatted (e.g. "0.94"). */
  centerLabel: string;
  centerCaption?: string;
  size?: number;
  stroke?: number;
}) {
  const v = clamp01(value);
  const cx = size / 2;
  const cy = size / 2;
  const r = (size - stroke) / 2 - 2;
  const startAngle = -135;
  const endAngle = 135;
  const valueAngle = startAngle + (endAngle - startAngle) * v;

  return (
    <ChartFrame
      title={title}
      desc={desc}
      width={size}
      height={size}
      table={[[title, centerLabel]]}
    >
      <path
        className="gauge__track"
        d={describeArc(cx, cy, r, startAngle, endAngle)}
        strokeWidth={stroke}
      />
      <path
        className="gauge__value"
        d={describeArc(cx, cy, r, startAngle, valueAngle)}
        strokeWidth={stroke}
      />
      <text
        className="gauge__num"
        x={cx}
        y={cy - 2}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={size * 0.24}
      >
        {centerLabel}
      </text>
      {centerCaption ? (
        <text
          className="gauge__cap"
          x={cx}
          y={cy + size * 0.16}
          textAnchor="middle"
          dominantBaseline="central"
        >
          {centerCaption}
        </text>
      ) : null}
    </ChartFrame>
  );
}
