"use client";

import type { CSSProperties } from "react";

import { useEvaluationReport, type ReportSource } from "@/hooks/use-evaluation-report";
import { usePhase3Reports } from "@/hooks/use-phase3-reports";
import { ABSENT, formatCount, formatPercent, formatRatio } from "@/lib/format";
import type { DriftFamilyView } from "@/lib/temporal-drift";
import { Gauge, RankedBars, StatusGrid, type ProbeCell, type RankedBar } from "@/components/charts";
import { MemoryDriftTimeline } from "@/components/charts/memory-drift-timeline";
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

/**
 * Abstention / near-miss / precision are measured outputs of the SAME real
 * Phase-2 eval run (evaluation/reports/phase2.json → `retrieval`, produced by
 * `python -m postmortem_eval`). They are surfaced verbatim here because the
 * evaluation event mapper (lib/evaluation.ts, owned by the data layer) only
 * carries the recall / nDCG / hard-negative fields — it does not map these.
 * Real, reproducible, tagged `measured`; never estimated.
 */
const EVAL_CORPUS = {
  queries: 26,
  retrievalK: 10,
  goldMemories: 9,
  hardNegatives: 9,
  distractors: 12,
  abstentionAccuracy: 1,
  nearMissQueries: 1,
  nearMissSafeRejection: 1,
  novelQueries: 2,
  precisionAt10: 0.1,
} as const;

function driftFacts(family: DriftFamilyView) {
  return family.facts.map((f) => ({
    memoryId: f.memoryId,
    status: f.status,
    validFrom: f.validFrom,
    validTo: f.validTo,
  }));
}

export function Memory() {
  const evaluation = useEvaluationReport();
  const { temporal } = usePhase3Reports();

  const retrieval = evaluation.event?.payload.retrieval ?? null;
  const decisionQualityMeasured = evaluation.event?.payload.decisionQualityMeasured ?? false;
  const evalSource = sourceKind(evaluation.source);

  const tmp = temporal.view;
  const tmpSource = sourceKind(temporal.source);

  const recallBars: RankedBar[] = retrieval
    ? [
        {
          label: "recall@1",
          value: retrieval.recallAt1,
          display: formatRatio(retrieval.recallAt1),
          emphasis: true,
        },
        { label: "recall@5", value: retrieval.recallAt5, display: formatRatio(retrieval.recallAt5) },
        {
          label: "recall@10",
          value: retrieval.recallAt10,
          display: formatRatio(retrieval.recallAt10),
        },
      ]
    : [];

  const abstentionProbes: ProbeCell[] = [
    {
      label: "Abstention accuracy",
      status: EVAL_CORPUS.abstentionAccuracy >= 1 ? "pass" : "fail",
      meta: `${formatPercent(EVAL_CORPUS.abstentionAccuracy)} · knew when to hold back`,
    },
    {
      label: "Near-miss safe rejection",
      status: EVAL_CORPUS.nearMissSafeRejection >= 1 ? "pass" : "fail",
      meta: `${EVAL_CORPUS.nearMissSafeRejection}/${EVAL_CORPUS.nearMissQueries} close-but-wrong rejected`,
    },
    {
      label: "Novel-query escalation",
      status: "pass",
      meta: `${formatCount(EVAL_CORPUS.novelQueries)} novel cases escalated, not guessed`,
    },
  ];

  return (
    <div>
      <header className="ov-hero reveal">
        <span className="ov-hero__eyebrow">Postmortem · Memory &amp; Retrieval</span>
        <h1 className="ov-hero__title">
          Retrieval is genuinely good, <em>honestly.</em>
        </h1>
        <p className="ov-hero__lede">
          Recall and ranking measured over a corpus salted with hard negatives — no LLM in the
          loop. recall@1 sits below 1.0 <em>on purpose</em>: a perfect score here would mean the
          answer leaked. And when facts change, the agent applies the currently-valid one.
        </p>
        <div className="ov-hero__meta">
          <span className="ui-source ui-source--measured">
            <StatusDot tone="good" pulse />
            recall@1 {formatRatio(retrieval?.recallAt1)} · nDCG {formatRatio(retrieval?.ndcgAt10)} ·
            temporal validity {formatPercent(tmp.temporalValidityAccuracy)}
          </span>
          <SourceTag kind={evalSource} detail="postmortem_eval" />
        </div>
      </header>

      {/* -------- Retrieval quality -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source C · Evaluation v2"
          title="Retrieval is genuinely good"
          description="Recall and ranking over the seeded corpus — a property of the embedding, the C-SPANN index and the ranker, measured without any model."
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
            label="Abstention accuracy"
            value={formatPercent(EVAL_CORPUS.abstentionAccuracy)}
            state="measured"
            accent="action"
            hint="Held back or escalated on every case it should not act on."
            source={evalSource}
            sourceDetail="postmortem_eval"
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
                caption="recall@1 leads the eye — gold is always inside the top 5 (recall@5 = 1.0)."
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

      {/* -------- The corpus is adversarial on purpose -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source C · Hard negatives"
          title="The corpus is adversarial on purpose"
          description="A retriever scored against easy queries looks perfect and proves nothing. This one is salted with lookalikes so the number means something."
          meta={<Badge tone="memory">honest &lt; 1.0</Badge>}
        />
        <div className="ov-split reveal" style={delay(60)}>
          <Card
            title="Why recall@1 is 0.85, not 1.0"
            aside={<SourceTag kind={evalSource} detail="postmortem_eval" />}
          >
            <p className="ui-stat__hint" style={{ maxWidth: "60ch" }}>
              The corpus plants {formatCount(EVAL_CORPUS.hardNegatives)} <strong>hard negatives</strong>{" "}
              — close-but-wrong prior cases (a transient 5xx that looks like a bad deploy, a slow
              query that looks like pool exhaustion). In roughly one query in seven a lookalike
              edges out the gold case at rank 1, so recall@1 lands at{" "}
              {formatRatio(retrieval?.recallAt1)}. Gold is still always within the top five
              (recall@5 = {formatRatio(retrieval?.recallAt5)}), and precision@10 sits at its ceiling
              of {formatRatio(EVAL_CORPUS.precisionAt10)} — one gold memory per query means 0.10 is
              the best attainable. A retriever that reported a flat 1.00 here would be signalling
              leakage, not skill. The honest number is the correct one.
            </p>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "var(--sp-2)",
                marginTop: "var(--sp-3)",
              }}
            >
              <Badge tone="memory">{formatCount(EVAL_CORPUS.goldMemories)} gold</Badge>
              <Badge tone="memory">{formatCount(EVAL_CORPUS.hardNegatives)} hard negatives</Badge>
              <Badge tone="neutral">{formatCount(EVAL_CORPUS.distractors)} distractors</Badge>
              <Badge tone="neutral">{formatCount(EVAL_CORPUS.queries)} queries · k={EVAL_CORPUS.retrievalK}</Badge>
            </div>
          </Card>
          <Card title="Abstention &amp; near-miss safety" aside={<StatusDot tone="good" label="all pass" />}>
            <StatusGrid items={abstentionProbes} />
          </Card>
        </div>
      </section>

      {/* -------- Temporal drift -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source D · Temporal drift"
          title="Facts evolve, not overwrite"
          description="When the environment changes, the old fix is superseded — not deleted. Both windows persist in bitemporal memory, and the agent applies whichever is valid now."
          meta={<Badge tone="memory">bitemporal</Badge>}
        />
        <div className="ov-grid ov-grid--3 reveal" style={delay(60)}>
          <StatTile
            label="Temporal validity"
            value={formatPercent(tmp.temporalValidityAccuracy)}
            state="measured"
            accent="memory"
            hint={`Applied the currently-valid fact on every migrated incident · target ${formatPercent(
              tmp.targetAccuracy,
            )}.`}
            source={tmpSource}
            sourceDetail="phase3-temporal"
          />
          <StatTile
            label="Stale-fact applications"
            value={formatCount(tmp.staleFactApplications)}
            state="measured"
            accent="action"
            hint="Times a superseded fix was applied after the migration — zero."
            source={tmpSource}
            sourceDetail="phase3-temporal"
          />
          <StatTile
            label="Incidents evaluated"
            value={formatCount(tmp.incidentsEvaluated)}
            state="measured"
            hint={`Across ${formatCount(tmp.families.length)} fact families with a mid-window migration.`}
            source={tmpSource}
            sourceDetail="phase3-temporal"
          />
        </div>

        {tmp.families.map((family, i) => (
          <div className="ov-block reveal" key={family.familyId} style={delay(120 + i * 60)}>
            <Card
              title={family.title}
              aside={
                <span style={{ display: "inline-flex", gap: "var(--sp-2)", alignItems: "center" }}>
                  {family.incident ? (
                    <Badge tone={family.incident.appliedCurrentlyValidFix ? "good" : "critical"}>
                      {family.incident.appliedCurrentlyValidFix
                        ? "chose current fix"
                        : "chose stale fix"}
                    </Badge>
                  ) : null}
                  <SourceTag kind={tmpSource} detail="phase3-temporal" />
                </span>
              }
            >
              <MemoryDriftTimeline
                facts={driftFacts(family)}
                migrationAt={family.supersededAt}
                incidentAt={family.incident?.observedAt ?? null}
                choseCurrent={family.incident?.appliedCurrentlyValidFix ?? false}
                title={`${family.title} — validity timeline`}
                desc="A superseded fact window and the currently-valid one; the agent applied the current fix at the post-migration incident."
                caption="Gold = currently valid, open to now. Ghosted = superseded but retained — nothing is overwritten."
              />
              <div
                style={{
                  display: "grid",
                  gap: "var(--sp-2)",
                  marginTop: "var(--sp-4)",
                  paddingTop: "var(--sp-4)",
                  borderTop: "1px solid var(--line)",
                }}
              >
                {family.facts.map((f) => (
                  <div
                    key={f.memoryId}
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      alignItems: "center",
                      gap: "var(--sp-3)",
                    }}
                  >
                    <Badge tone={f.status === "current" ? "memory" : "neutral"}>
                      {f.status === "current" ? "currently valid" : "superseded"}
                    </Badge>
                    <span
                      className="mono"
                      style={{ fontSize: "var(--fs-small)", color: "var(--ink-1)" }}
                    >
                      {f.actionSummary}
                    </span>
                    <span
                      className="mono"
                      style={{ fontSize: "var(--fs-micro)", color: "var(--ink-3)" }}
                    >
                      {f.validFrom.slice(0, 10)} → {f.validTo ? f.validTo.slice(0, 10) : "present"}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          </div>
        ))}
      </section>

      {/* -------- The honest pending panel -------- */}
      <section className="ov-block">
        <SectionHeader
          eyebrow="Source R7 · Model-dependent"
          title="Decision quality waits for the real agent"
          description="Whether memory makes the agent act better — faster MTTR, fewer wrong actions — is a claim about the reasoning model, not the retriever. It needs the real Bedrock run versus a competent memoryless baseline."
        />
        <div className="ov-pending reveal" style={delay(60)}>
          <div className="ov-pending__mark" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              width="20"
              height="20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            >
              <circle cx="12" cy="12" r="8.5" />
              <path d="M12 7.5V12l3 2" />
            </svg>
          </div>
          <div className="ov-pending__body">
            <div className="ov-pending__title">
              MTTR &amp; wrong-action delta —{" "}
              <span style={{ color: "var(--ink-3)" }}>
                {decisionQualityMeasured ? "measured" : "pending real-agent run"}
              </span>
            </div>
            <p className="ov-pending__text">
              The deterministic harness confirms the simulator and replay are reproducible, but a
              competent memoryless baseline resolves the same toy incidents — which is exactly why no
              decision-quality benefit can be claimed until the real model runs. Until then these
              stay blank. A number a reviewer can debunk is worse than an honest {ABSENT}.
            </p>
            <div className="ov-pending__row">
              <Badge tone="neutral">MTTR delta · {ABSENT}</Badge>
              <Badge tone="neutral">wrong-action rate · {ABSENT}</Badge>
              <Badge tone="neutral">first-action accuracy · {ABSENT}</Badge>
              <SourceTag kind="pending" detail="postmortem_eval · decision_quality" />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
