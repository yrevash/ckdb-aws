import { clamp, linearScale } from "@/lib/chart";
import { ChartFrame } from "@/components/charts/chart-frame";

/**
 * Leaseholder distribution across the region kill — a bespoke stacked-bar chart
 * (one stacked row per failover phase: before → during → after). It answers
 * "where did the leaseholders go when we killed their region?" using the REAL
 * `range_snapshot.*.leaseholder_region_counts` from phase3-resilience.json.
 *
 * Colour follows the region (fixed identity, never rank): the killed region is
 * painted coral (the `--kill` semantic, matching the LivenessTimeline outage
 * band), the surviving regions take validated categorical series slots. Two+
 * series ⇒ a legend is always present (dataviz rule); segment counts are direct
 * labels, never colour-alone.
 */

export type LeaseholderPhaseId = "before" | "during" | "after";

export type LeaseholderPhase = {
  id: LeaseholderPhaseId;
  label: string;
  /** leaseholder count per region, in the topology's region order (0 when absent). */
  counts: { region: string; leaseholders: number }[];
  /** total ranges the leaseholders are spread across (the bar's full width). */
  total: number;
};

export type LeaseholderSnapshot = {
  regions: string[];
  killedRegion: string;
  table: string;
  phases: LeaseholderPhase[];
};

const PHASE_KEYS: { id: LeaseholderPhaseId; key: string; label: string }[] = [
  { id: "before", key: "before_kill", label: "Before kill" },
  { id: "during", key: "during_outage", label: "During outage" },
  { id: "after", key: "after_recovery", label: "After recovery" },
];

/** Validated categorical slots for the surviving (non-killed) regions, in order. */
const SURVIVOR_COLORS = ["var(--series-1)", "var(--series-3)", "var(--series-7)", "var(--series-4)"];

/**
 * Assign each region a stable colour: the killed region is coral (`--kill`),
 * every other region takes the next validated series slot in region order. The
 * mapping is by region identity, so a region keeps its colour across all charts.
 */
export function regionColors(regions: string[], killedRegion: string): Record<string, string> {
  const map: Record<string, string> = {};
  let survivor = 0;
  for (const region of regions) {
    if (region === killedRegion) {
      map[region] = "var(--kill)";
    } else {
      map[region] = SURVIVOR_COLORS[survivor % SURVIVOR_COLORS.length];
      survivor += 1;
    }
  }
  return map;
}

function countsRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object") return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

/**
 * Parse the real report's `range_snapshot` into a typed leaseholder snapshot.
 * Returns `null` (→ caller falls back to the labelled replay fixture) when the
 * snapshot or topology is missing — never a fabricated distribution.
 */
export function leaseholderSnapshotFromReport(value: unknown): LeaseholderSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const report = value as {
    range_snapshot?: Record<string, unknown>;
    topology?: { regions?: unknown; killed_region?: unknown };
  };
  const snapshot = report.range_snapshot;
  const topology = report.topology;
  if (!snapshot || typeof snapshot !== "object" || !topology) return null;
  if (!Array.isArray(topology.regions) || topology.regions.some((r) => typeof r !== "string")) {
    return null;
  }
  const regions = topology.regions as string[];
  const killedRegion = typeof topology.killed_region === "string" ? topology.killed_region : "";

  // First pass: read each phase's raw counts and total. Any region that shows
  // leaseholders but isn't in the declared topology still gets a column, so a
  // real distribution is never silently dropped.
  const seen = new Set<string>(regions);
  let table = "episodic_events";
  const raw: { id: LeaseholderPhaseId; label: string; counts: Record<string, number>; total: number }[] = [];

  for (const { id, key, label } of PHASE_KEYS) {
    const phaseSnap = snapshot[key];
    if (!phaseSnap || typeof phaseSnap !== "object") return null;
    const record = phaseSnap as Record<string, unknown>;
    if (typeof record.table === "string") table = record.table;
    const counts = countsRecord(record.leaseholder_region_counts);
    for (const region of Object.keys(counts)) seen.add(region);
    const rangeCount = typeof record.range_count === "number" ? record.range_count : null;
    raw.push({
      id,
      label,
      counts,
      total: rangeCount ?? Object.values(counts).reduce((sum, n) => sum + n, 0),
    });
  }

  // Second pass: project every phase onto the same ordered region set.
  const orderedRegions = [...regions, ...[...seen].filter((r) => !regions.includes(r))];
  const phases: LeaseholderPhase[] = raw.map((phase) => ({
    id: phase.id,
    label: phase.label,
    total: phase.total,
    counts: orderedRegions.map((region) => ({ region, leaseholders: phase.counts[region] ?? 0 })),
  }));

  return { regions: orderedRegions, killedRegion, table, phases };
}

export function ResilienceLeaseholders({
  snapshot,
  title,
  caption,
}: {
  snapshot: LeaseholderSnapshot;
  title: string;
  caption?: string;
}) {
  const { regions, killedRegion, phases } = snapshot;
  const colors = regionColors(regions, killedRegion);

  const width = 520;
  const labelWidth = 118;
  const rowHeight = 54;
  const barHeight = 22;
  const padY = 8;
  const trackW = width - labelWidth - 16;
  const height = phases.length * rowHeight + padY * 2;

  const maxTotal = Math.max(1, ...phases.map((p) => p.total));
  const x = linearScale(0, maxTotal, 0, trackW);

  return (
    <ChartFrame
      title={title}
      width={width}
      height={height}
      caption={
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--sp-3)", alignItems: "center" }}>
          {regions.map((region) => {
            const killed = region === killedRegion;
            return (
              <span
                key={region}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "var(--sp-2)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--fs-micro)",
                  color: "var(--ink-2)",
                }}
              >
                <span
                  aria-hidden="true"
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 3,
                    background: colors[region],
                  }}
                />
                {region}
                {killed ? (
                  <span style={{ color: "var(--kill)", fontWeight: 600 }}>· killed</span>
                ) : null}
              </span>
            );
          })}
          {caption ? (
            <span style={{ color: "var(--ink-3)", fontSize: "var(--fs-small)" }}>{caption}</span>
          ) : null}
        </div>
      }
      table={phases.flatMap((p) =>
        p.counts
          .filter((c) => c.leaseholders > 0)
          .map((c): [string, string] => [`${p.label} · ${c.region}`, `${c.leaseholders} leaseholders`]),
      )}
    >
      {phases.map((phase, i) => {
        const cy = padY + i * rowHeight + rowHeight / 2;
        const barY = cy - barHeight / 2;
        let cursor = labelWidth;
        return (
          <g key={phase.id}>
            <text
              className="chart__value"
              x={labelWidth - 12}
              y={cy}
              textAnchor="end"
              dominantBaseline="central"
            >
              {phase.label}
            </text>
            {/* recessive track behind the stack */}
            <rect
              className="rbar__track"
              x={labelWidth}
              y={barY}
              width={trackW}
              height={barHeight}
              rx={5}
            />
            {phase.counts.map((c) => {
              if (c.leaseholders <= 0) return null;
              const w = clamp(x(c.leaseholders), 0, trackW);
              const segX = cursor;
              cursor += w;
              const killed = c.region === killedRegion;
              return (
                <g key={c.region}>
                  <rect
                    x={segX}
                    y={barY}
                    width={Math.max(2, w)}
                    height={barHeight}
                    rx={2}
                    style={{ fill: colors[c.region] }}
                  />
                  {w > 20 ? (
                    <text
                      x={segX + w / 2}
                      y={cy}
                      textAnchor="middle"
                      dominantBaseline="central"
                      style={{
                        fill: killed ? "var(--accent-ink)" : "#ffffff",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        fontWeight: 600,
                      }}
                    >
                      {c.leaseholders}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
        );
      })}
    </ChartFrame>
  );
}
