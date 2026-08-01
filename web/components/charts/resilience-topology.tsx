import { ChartFrame } from "@/components/charts/chart-frame";
import {
  regionColors,
  type LeaseholderSnapshot,
} from "@/components/charts/resilience-leaseholders";

/**
 * Region topology across the kill — a bespoke SVG that lays the cluster's
 * regions out as connected nodes and highlights the one that was killed
 * (coral fill + border + a "region down" flag). Under each region it prints the
 * REAL leaseholder migration (before → after) from the range snapshot, so the
 * story reads at a glance: leaseholders were pinned to the killed region, then
 * moved onto the survivors. Colour follows the region identity, matching the
 * leaseholder stack.
 */
export function ResilienceTopology({
  regions,
  killedRegion,
  primaryRegion,
  replicationFactor,
  nodesTotal,
  snapshot,
  title,
  caption,
}: {
  regions: string[];
  killedRegion: string;
  primaryRegion: string;
  replicationFactor: number;
  nodesTotal: number;
  snapshot: LeaseholderSnapshot;
  title: string;
  caption?: string;
}) {
  const colors = regionColors(regions, killedRegion);
  const before = snapshot.phases.find((p) => p.id === "before");
  const after = snapshot.phases.find((p) => p.id === "after");
  const leaseFor = (region: string, phase = before) =>
    phase?.counts.find((c) => c.region === region)?.leaseholders ?? 0;

  const width = 560;
  const boxW = 150;
  const boxH = 104;
  const gap = (width - boxW * regions.length) / (regions.length + 1);
  const top = 28;
  const height = top + boxH + 44;
  const boxX = (i: number) => gap + i * (boxW + gap);
  const midY = top + boxH / 2;

  return (
    <ChartFrame
      title={title}
      width={width}
      height={height}
      caption={
        caption ? (
          <span style={{ color: "var(--ink-3)", fontSize: "var(--fs-small)" }}>{caption}</span>
        ) : (
          <span style={{ color: "var(--ink-3)", fontSize: "var(--fs-small)" }}>
            {nodesTotal} nodes · RF {replicationFactor} · leaseholders shown before → after
          </span>
        )
      }
      table={regions.map((region): [string, string] => [
        region,
        `${leaseFor(region, before)} → ${leaseFor(region, after)} leaseholders${
          region === killedRegion ? " (killed)" : region === primaryRegion ? " (primary)" : ""
        }`,
      ])}
    >
      {/* faint cluster links between adjacent regions */}
      {regions.slice(0, -1).map((region, i) => (
        <line
          key={`link-${region}`}
          x1={boxX(i) + boxW}
          y1={midY}
          x2={boxX(i + 1)}
          y2={midY}
          stroke="var(--line-strong)"
          strokeWidth={1}
          strokeDasharray="2 4"
        />
      ))}

      {regions.map((region, i) => {
        const killed = region === killedRegion;
        const primary = region === primaryRegion;
        const x = boxX(i);
        const beforeN = leaseFor(region, before);
        const afterN = leaseFor(region, after);
        return (
          <g key={region}>
            <rect
              x={x}
              y={top}
              width={boxW}
              height={boxH}
              rx={10}
              style={{
                fill: killed ? "var(--kill-weak)" : "var(--surface-inset)",
                stroke: killed
                  ? "var(--kill)"
                  : primary
                    ? "var(--accent-line)"
                    : "var(--line-strong)",
                strokeWidth: killed ? 1.5 : 1,
              }}
            />
            {/* region colour chip */}
            <rect x={x + 14} y={top + 16} width={12} height={12} rx={3} style={{ fill: colors[region] }} />
            <text
              x={x + 34}
              y={top + 22}
              dominantBaseline="central"
              style={{ fill: "var(--ink-1)", fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}
            >
              {region}
            </text>

            <text
              x={x + 14}
              y={top + 46}
              style={{
                fill: killed ? "var(--kill)" : "var(--ink-3)",
                fontFamily: "var(--font-mono)",
                fontSize: 10.5,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
              }}
            >
              {killed ? "region down" : primary ? "primary" : "survivor"}
            </text>

            {/* leaseholder migration for this region */}
            <text
              x={x + 14}
              y={top + 74}
              style={{ fill: "var(--ink-2)", fontFamily: "var(--font-mono)", fontSize: 11.5 }}
            >
              leaseholders
            </text>
            <text
              x={x + boxW - 14}
              y={top + 74}
              textAnchor="end"
              style={{
                fill: killed && afterN === 0 ? "var(--kill)" : "var(--ink-1)",
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              {beforeN} → {afterN}
            </text>
          </g>
        );
      })}
    </ChartFrame>
  );
}
