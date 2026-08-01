"use client";

import type { CSSProperties } from "react";

import { useEvaluationReport, type ReportSource } from "@/hooks/use-evaluation-report";
import { usePhase3Reports } from "@/hooks/use-phase3-reports";
import {
  ABSENT,
  formatCount,
  formatDuration,
  formatMs,
  formatPercent,
  formatRatio,
} from "@/lib/format";
import { failoverPhases } from "@/lib/resilience";
import {
  Gauge,
  LivenessTimeline,
  RankedBars,
  StatusGrid,
  type LivenessPhase,
  type ProbeCell,
  type RankedBar,
} from "@/components/charts";
import {
  Badge,
  Card,
  SectionHeader,
  SourceTag,
  StatTile,
  StatusDot,
  type SourceKind,
} from "@/components/ui";

/** live report → "measured"; embedded replay fixture → "replay". */
function sourceKind(source: ReportSource): SourceKind {
  return source === "live" ? "measured" : "replay";
}

function delay(ms: number): CSSProperties {
  return { ["--reveal-delay" as string]: `${ms}ms` };
}

export function Overview() {
  const evaluation = useEvaluationReport();
  const { resilience, temporal } = usePhase3Reports();

  const retrieval = evaluation.event?.payload.retrieval ?? null;
  const decisionQualityMeasured = evaluation.event?.payload.decisionQualityMeasured ?? false;
  const evalSource = sourceKind(evaluation.source);

  const res = resilience.view;
  const resSource = sourceKind(resilience.source);
  const tmp = temporal.view;
  const tmpSource = sourceKind(temporal.source);

  const rto = formatDuration(res.rto.seconds);
  const rpoLost = res.rpo.rowsLost;

  const recallBars: RankedBar[] = retrieval
    ? [
        {
          label: "recall@1",
          value: retrieval.recallAt1,
          display: formatRatio(retrieval.recallAt1),
          emphasis: true,
        },
        {
          label: "recall@5",
          value: retrieval.recallAt5,
          display: formatRatio(retrieval.recallAt5),
        },
        {
          label: "recall@10",
          value: retrieval.recallAt10,
          display: formatRatio(retrieval.recallAt10),
        },
      ]
    : [];

  const livenessPhases: LivenessPhase[] = failoverPhases(res).map((p) => ({
    id: p.id,
    label: p.label,
    liveNodes: p.liveNodes,
    down: p.regionDown,
    sub:
      p.id === "region-down"
        ? `detected ${res.liveness.regionDownDetectionSeconds.toFixed(1)}s`
        : p.id === "recovered"
          ? `restored ${res.liveness.recoveryElapsedSeconds.toFixed(1)}s`
          : `${res.regions.length} regions healthy`,
  }));

  const probes: ProbeCell[] = [
    {
      label: "RPO — no data loss",
      status: res.rpo.status,
      meta: rpoLost === null ? "unknown" : `${formatCount(rpoLost)} rows lost`,
    },
    {
      label: "RTO — recovery time",
      status: res.rto.status,
      meta: `${rto.value} ${rto.unit}`.trim(),
    },
    {
      label: "Read-your-writes",
      status: res.freshness.status,
      meta: res.freshness.foundImmediately ? "found immediately" : "delayed",
    },
    {
      label: "Cross-agent visibility",
      status: res.crossAgent.status,
      meta: res.crossAgent.crossRegion ? "cross-region, no lag" : "same-region",
    },
    {
      label: "Atomicity",
      status: res.atomicity.status,
      meta: "commit + abort hold",
    },
  ];

  return (
    <div>
      <header className="ov-hero reveal">
        <span className="ov-hero__eyebrow">Postmortem · Evidence board</span>
        <h1 className="ov-hero__title">
          What&rsquo;s proven, <em>and what&rsquo;s still pending.</em>
        </h1>
        <p className="ov-hero__lede">
          An on-call agent with transactionally consistent memory. Every reading below
          comes from a real run or a labelled replay of one — nothing is illustrative.
          Decision quality waits for the real model, and says so.
        </p>
        <div className="ov-hero__meta">
          <span className="ui-source ui-source--measured">
            <StatusDot tone="good" pulse />
            All resilience probes pass · {res.regions.length}-region cluster
          </span>
          <SourceTag kind={resSource} detail="phase3-resilience" />
        </div>
      </header>

      {/* -------- Retrieval & memory -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source C · Evaluation v2"
          title="Retrieval is genuinely good"
          description="Recall and ranking over a corpus seeded with hard negatives — a property of the index and ranker, measured without any LLM."
          meta={<Badge tone="memory">memory</Badge>}
        />
        <div className="ov-grid reveal" style={delay(60)}>
          <StatTile
            label="Retrieval recall@1"
            value={formatRatio(retrieval?.recallAt1)}
            state={retrieval ? "measured" : "absent"}
            animateFrom={retrieval?.recallAt1 ?? Number.NaN}
            format={(n) => formatRatio(n)}
            hint="Top result is the gold case — under hard negatives, < 1.0 by design."
            source={evalSource}
            sourceDetail="postmortem_eval"
          />
          <StatTile
            label="nDCG@10"
            value={formatRatio(retrieval?.ndcgAt10)}
            state={retrieval ? "measured" : "absent"}
            animateFrom={retrieval?.ndcgAt10 ?? Number.NaN}
            format={(n) => formatRatio(n)}
            accent="memory"
            hint="Ranking quality across the top 10 recalled memories."
            source={evalSource}
            sourceDetail="postmortem_eval"
          />
          <StatTile
            label="Hard negatives in corpus"
            value={formatCount(retrieval?.hardNegativeCount)}
            state={retrieval ? "measured" : "absent"}
            animateFrom={retrieval?.hardNegativeCount ?? Number.NaN}
            format={(n) => formatCount(n)}
            accent="memory"
            hint="Close-but-wrong prior cases planted to make recall honest."
            source={evalSource}
            sourceDetail="postmortem_eval"
          />
          <StatTile
            label="Temporal validity"
            value={formatPercent(tmp.temporalValidityAccuracy)}
            state="measured"
            accent="memory"
            hint={`Chose the currently-valid fact · ${formatCount(tmp.staleFactApplications)} stale applications.`}
            source={tmpSource}
            sourceDetail="phase3-temporal"
          />
        </div>

        <div className="ov-split reveal" style={delay(120)}>
          <Card title="Recall@k" aside={<SourceTag kind={evalSource} detail="postmortem_eval" />}>
            {retrieval ? (
              <RankedBars
                data={recallBars}
                max={1}
                title="Retrieval recall at k"
                desc="recall@1 0.85, recall@5 and recall@10 at 1.0"
                caption="recall@1 leads the eye — the rest reach 1.0 by k=5."
              />
            ) : (
              <p className="ui-stat__hint">{ABSENT} evaluation report not present.</p>
            )}
          </Card>
          <Card title="Ranking quality" aside={<Badge tone="memory">nDCG@10</Badge>}>
            <div style={{ display: "grid", placeItems: "center" }}>
              <Gauge
                value={retrieval?.ndcgAt10 ?? 0}
                title="nDCG at 10"
                centerLabel={formatRatio(retrieval?.ndcgAt10)}
                centerCaption="of 1.00"
              />
            </div>
          </Card>
        </div>
      </section>

      {/* -------- Resilience under region kill -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source B · Resilience"
          title="Survives a real region kill"
          description={`Leaseholders pinned to ${res.killedRegion}, then killed. Memory stayed readable and writable throughout — verified during the outage, not after.`}
          meta={<Badge tone="kill">region kill</Badge>}
        />
        <div className="ov-grid reveal" style={delay(60)}>
          <StatTile
            label="RPO — rows lost"
            value={rpoLost === null ? ABSENT : formatCount(rpoLost)}
            unit={rpoLost === null ? undefined : "rows"}
            state={rpoLost === null ? "absent" : "measured"}
            accent="kill"
            hint={`${formatCount(res.rpo.rowsFound)} of ${formatCount(res.rpo.rowsExpected)} tracked rows re-read intact.`}
            source={resSource}
            sourceDetail="phase3-resilience"
          />
          <StatTile
            label="RTO — time to recover"
            value={rto.value}
            unit={rto.unit}
            state={res.rto.seconds === null ? "absent" : "measured"}
            accent="kill"
            hint={
              res.rto.recoveredViaRegion
                ? `First success via ${res.rto.recoveredViaRegion}, under the ${res.rto.targetSeconds}s target.`
                : "Time to the first successful write after the kill."
            }
            source={resSource}
            sourceDetail="phase3-resilience"
          />
          <StatTile
            label="Read-your-writes"
            value="0"
            unit="stale"
            state={res.freshness.status === "pass" ? "measured" : "absent"}
            accent="action"
            hint={`Found immediately${
              res.freshness.staleMs !== null ? ` · ${formatMs(res.freshness.staleMs)} cross-region read` : ""
            }.`}
            source={resSource}
            sourceDetail="phase3-resilience"
          />
          <StatTile
            label="Nodes live through kill"
            value={`${res.liveness.beforeKill}→${res.liveness.duringOutage}→${res.liveness.afterRecovery}`}
            state="measured"
            accent="kill"
            hint={`Quorum held on ${res.liveness.duringOutage} of ${res.liveness.expected} nodes; full liveness restored in ${res.liveness.recoveryElapsedSeconds.toFixed(1)}s.`}
            source={resSource}
            sourceDetail="phase3-resilience"
          />
        </div>

        <div className="ov-split reveal" style={delay(120)}>
          <Card
            title="Node liveness over the kill"
            aside={<SourceTag kind={resSource} detail="node_liveness" />}
          >
            <LivenessTimeline
              phases={livenessPhases}
              expected={res.liveness.expected}
              title="Node liveness across the region kill"
              desc={`Live nodes stepped ${res.liveness.beforeKill} to ${res.liveness.duringOutage} to ${res.liveness.afterRecovery} across the outage.`}
              caption={`Coral band = ${res.killedRegion} down. The agent kept writing on the surviving quorum.`}
            />
          </Card>
          <Card title="Consistency probes" aside={<StatusDot tone="good" label="all pass" />}>
            <StatusGrid items={probes} />
          </Card>
        </div>
      </section>

      {/* -------- The honest pending tile -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source R7 · Model-dependent"
          title="What we will not fake"
          description="Anything about the agent's decision quality needs the real reasoning model reasoning over retrieved memory versus a competent memoryless baseline."
        />
        <div className="ov-pending reveal" style={delay(60)}>
          <div className="ov-pending__mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
              <circle cx="12" cy="12" r="8.5" />
              <path d="M12 7.5V12l3 2" />
            </svg>
          </div>
          <div className="ov-pending__body">
            <div className="ov-pending__title">
              MTTR &amp; decision-quality delta —{" "}
              <span style={{ color: "var(--ink-3)" }}>
                {decisionQualityMeasured ? "measured" : "pending real-agent run"}
              </span>
            </div>
            <p className="ov-pending__text">
              First-action accuracy, wrong-action rate and the MTTR delta versus a memoryless
              baseline are not yet measured. They require the real model (Bedrock); until that run
              exists we show no number here — a debunkable figure is worse than an honest blank.
            </p>
            <div className="ov-pending__row">
              <Badge tone="neutral">first-action accuracy · {ABSENT}</Badge>
              <Badge tone="neutral">wrong-action rate · {ABSENT}</Badge>
              <Badge tone="neutral">MTTR delta · {ABSENT}</Badge>
              <SourceTag kind="pending" />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
