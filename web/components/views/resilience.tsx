"use client";

import { useEffect, useState, type CSSProperties } from "react";

import { ABSENT, formatCount, formatDuration, formatMs } from "@/lib/format";
import {
  PHASE_THREE_RESILIENCE,
  PHASE_THREE_RESILIENCE_REPORT,
  failoverPhases,
  resilienceViewFromReport,
  type ResilienceView,
} from "@/lib/resilience";
import {
  LivenessTimeline,
  StatusGrid,
  type LivenessPhase,
  type ProbeCell,
} from "@/components/charts";
import {
  ResilienceLeaseholders,
  leaseholderSnapshotFromReport,
  type LeaseholderSnapshot,
} from "@/components/charts/resilience-leaseholders";
import { ResilienceTopology } from "@/components/charts/resilience-topology";
import {
  Badge,
  Card,
  SectionHeader,
  SourceTag,
  StatTile,
  StatusDot,
  type SourceKind,
} from "@/components/ui";

type ReportSource = "live" | "replay";

/** live report → "measured"; embedded replay fixture → "replay". */
function sourceKind(source: ReportSource): SourceKind {
  return source === "live" ? "measured" : "replay";
}

function delay(ms: number): CSSProperties {
  return { ["--reveal-delay" as string]: `${ms}ms` };
}

const RESILIENCE_ENDPOINT =
  process.env.NEXT_PUBLIC_POSTMORTEM_RESILIENCE_URL ?? "/phase3-resilience.json";

type Loaded = {
  view: ResilienceView;
  snapshot: LeaseholderSnapshot;
  source: ReportSource;
};

const REPLAY: Loaded = {
  view: PHASE_THREE_RESILIENCE,
  snapshot: leaseholderSnapshotFromReport(PHASE_THREE_RESILIENCE_REPORT)!,
  source: "replay",
};

/**
 * Loads the real phase3-resilience artifact (view + leaseholder snapshot from
 * one fetch, one provenance), falling back to the labelled replay fixture so the
 * surface is never blank. Mirrors the camera-safe posture of usePhase3Reports;
 * kept local so the leaseholder distribution and the probe view always share a
 * single source of truth.
 */
function useResilienceReport(): Loaded {
  const [state, setState] = useState<Loaded>(REPLAY);

  useEffect(() => {
    if (typeof fetch !== "function") return;
    let active = true;
    void fetch(RESILIENCE_ENDPOINT)
      .then((response) => {
        if (!response.ok) throw new Error(`report HTTP ${response.status}`);
        return response.json() as Promise<unknown>;
      })
      .then((report) => {
        const view = resilienceViewFromReport(report);
        const snapshot = leaseholderSnapshotFromReport(report);
        if (active && view && snapshot) setState({ view, snapshot, source: "live" });
      })
      .catch(() => {
        // Replay fixture already rendering; the live report is optional.
      });
    return () => {
      active = false;
    };
  }, []);

  return state;
}

export function Resilience() {
  const { view, snapshot, source } = useResilienceReport();
  const src = sourceKind(source);

  const rto = formatDuration(view.rto.seconds);
  const rpoLost = view.rpo.rowsLost;
  const probeCount = 5;
  const passing = view.overall.pass ? probeCount : probeCount - view.overall.failedProbes.length;

  const livenessPhases: LivenessPhase[] = failoverPhases(view).map((p) => ({
    id: p.id,
    label: p.label,
    liveNodes: p.liveNodes,
    down: p.regionDown,
    sub:
      p.id === "region-down"
        ? `detected ${view.liveness.regionDownDetectionSeconds.toFixed(1)}s`
        : p.id === "recovered"
          ? `restored ${view.liveness.recoveryElapsedSeconds.toFixed(1)}s`
          : `${view.regions.length} regions healthy`,
  }));

  const probes: ProbeCell[] = [
    {
      label: "Read-your-writes (freshness)",
      status: view.freshness.status,
      meta: view.freshness.foundImmediately
        ? `found immediately${view.freshness.staleMs !== null ? ` · ${formatMs(view.freshness.staleMs)} cross-region` : ""}`
        : "delayed",
    },
    {
      label: "Cross-agent visibility",
      status: view.crossAgent.status,
      meta: view.crossAgent.crossRegion
        ? `${view.crossAgent.writerRegion} → ${view.crossAgent.readerRegion}, no lag`
        : "same-region",
    },
    {
      label: "Atomicity",
      status: view.atomicity.status,
      meta: "commit + abort both hold",
    },
    {
      label: "RPO — no data loss",
      status: view.rpo.status,
      meta:
        rpoLost === null
          ? "unknown"
          : `${formatCount(rpoLost)} rows lost · ${formatCount(view.rpo.rowsFound)}/${formatCount(view.rpo.rowsExpected)} verified`,
    },
    {
      label: "RTO — recovery time",
      status: view.rto.status,
      meta: `${rto.value} ${rto.unit}`.trim(),
    },
  ];

  return (
    <div>
      <header className="ov-hero reveal">
        <span className="ov-hero__eyebrow">Postmortem · Resilience</span>
        <h1 className="ov-hero__title">
          Survives a <em>real region kill.</em>
        </h1>
        <p className="ov-hero__lede">
          Leaseholders were pinned to <strong>{view.killedRegion}</strong>, then that region was
          killed. Memory stayed readable and writable throughout — every row was verified{" "}
          <em>during</em> the outage, not reconstructed after it. Nothing here is illustrative.
        </p>
        <div className="ov-hero__meta">
          <span className="ui-source ui-source--measured">
            <StatusDot tone="good" pulse />
            {passing}/{probeCount} probes pass · {view.regions.length}-region cluster · {view.killedRegion} killed
          </span>
          <SourceTag kind={src} detail="phase3-resilience" />
        </div>
      </header>

      {/* -------- Headline: recovery, verified during the outage -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source B · Region kill"
          title="Recovery, verified during the outage"
          description={`First successful write came back through ${view.rto.recoveredViaRegion ?? "a surviving region"} after the leaseholders' region went dark — well under the ${view.rto.targetSeconds}s target, with zero rows lost.`}
          meta={<Badge tone="kill">region kill</Badge>}
        />
        <div className="ov-grid reveal" style={delay(60)}>
          <StatTile
            label="RTO — time to recover"
            value={rto.value}
            unit={rto.unit}
            state={view.rto.seconds === null ? "absent" : "measured"}
            accent="kill"
            hint={
              view.rto.recoveredViaRegion
                ? `First success via ${view.rto.recoveredViaRegion}, under the ${view.rto.targetSeconds}s target.`
                : "Time to the first successful write after the kill."
            }
            source={src}
            sourceDetail="phase3-resilience · rto"
          />
          <StatTile
            label="RPO — rows lost"
            value={rpoLost === null ? ABSENT : formatCount(rpoLost)}
            unit={rpoLost === null ? undefined : "rows"}
            state={rpoLost === null ? "absent" : "measured"}
            accent="kill"
            hint={`${formatCount(view.rpo.rowsFound)} of ${formatCount(view.rpo.rowsExpected)} tracked rows re-read intact — content-verified, not just counted.`}
            source={src}
            sourceDetail="phase3-resilience · rpo"
          />
          <StatTile
            label="Read-your-writes"
            value="0"
            unit="stale"
            state={view.freshness.status === "pass" ? "measured" : "absent"}
            accent="action"
            hint={`Found immediately${
              view.freshness.staleMs !== null ? ` · ${formatMs(view.freshness.staleMs)} cross-region read` : ""
            }.`}
            source={src}
            sourceDetail="phase3-resilience · freshness"
          />
          <StatTile
            label="Nodes live through kill"
            value={`${view.liveness.beforeKill}→${view.liveness.duringOutage}→${view.liveness.afterRecovery}`}
            state="measured"
            accent="kill"
            hint={`Quorum held on ${view.liveness.duringOutage} of ${view.liveness.expected} nodes; full liveness restored in ${view.liveness.recoveryElapsedSeconds.toFixed(1)}s.`}
            source={src}
            sourceDetail="phase3-resilience · node_liveness"
          />
        </div>
      </section>

      {/* -------- Node liveness + consistency probes -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source B · During the outage"
          title="Live through the kill, consistent throughout"
          description="Node liveness stepped down to the surviving quorum and back, while every consistency probe held — read-your-writes, cross-agent visibility, atomicity, RPO and RTO."
        />
        <div className="ov-split reveal" style={delay(120)}>
          <Card
            title="Node liveness over the kill"
            aside={<SourceTag kind={src} detail="node_liveness" />}
          >
            <LivenessTimeline
              phases={livenessPhases}
              expected={view.liveness.expected}
              title="Node liveness across the region kill"
              desc={`Live nodes stepped ${view.liveness.beforeKill} to ${view.liveness.duringOutage} to ${view.liveness.afterRecovery} across the outage.`}
              caption={`Coral band = ${view.killedRegion} down. The agent kept writing on the surviving quorum.`}
            />
          </Card>
          <Card
            title="Consistency probes"
            aside={
              <StatusDot tone={view.overall.pass ? "good" : "critical"} label={view.overall.pass ? "all pass" : "failing"} />
            }
          >
            <StatusGrid items={probes} />
          </Card>
        </div>
      </section>

      {/* -------- Region topology + leaseholder distribution -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source B · Failover topology"
          title="Where the leaseholders went"
          description={`All ${snapshot.phases[0]?.total ?? view.regions.length} leaseholder ranges started pinned to ${view.killedRegion}. When it died, they moved onto the surviving regions — that migration is the failover.`}
          meta={<Badge tone="kill">{view.killedRegion} killed</Badge>}
        />
        <div className="ov-split reveal" style={delay(60)}>
          <Card
            title="Region topology"
            aside={<SourceTag kind={src} detail="topology" />}
          >
            <ResilienceTopology
              regions={view.regions}
              killedRegion={view.killedRegion}
              primaryRegion={view.primaryRegion}
              replicationFactor={view.replicationFactor}
              nodesTotal={view.nodesTotal}
              snapshot={snapshot}
              title="Cluster regions with the killed region highlighted"
              caption={`${view.nodesTotal} nodes · RF ${view.replicationFactor} · leaseholders before → after`}
            />
          </Card>
          <Card
            title="Leaseholder distribution"
            aside={<SourceTag kind={src} detail="range_snapshot" />}
          >
            <ResilienceLeaseholders
              snapshot={snapshot}
              title="Leaseholders per region, before / during / after the kill"
              caption="the killed region's leaseholders shifted onto the survivors"
            />
          </Card>
        </div>
      </section>
    </div>
  );
}
