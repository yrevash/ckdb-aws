"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { ReportSource } from "@/hooks/use-phase3-reports";
import { failoverPhases, type ResilienceView } from "@/lib/resilience";

const PHASE_INTERVAL_MS = 2600;

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

type PanelNode = {
  id: string;
  region: string;
  zone: string;
  down: boolean;
};

function nodesForPhase(view: ResilienceView, regionDown: boolean): PanelNode[] {
  const perRegion = Math.max(1, Math.round(view.nodesTotal / Math.max(1, view.regions.length)));
  const zones = ["a", "b", "c"];
  return view.regions.flatMap((region) =>
    Array.from({ length: perRegion }, (_, index) => ({
      id: `${region}-${zones[index] ?? index}`,
      region,
      zone: zones[index] ?? String(index),
      down: regionDown && region === view.killedRegion,
    })),
  );
}

function SourceChip({ source }: { source: ReportSource }) {
  return (
    <span className={`res-source res-source--${source}`}>
      <span className="res-source__dot" aria-hidden="true" />
      {source === "live" ? "Live telemetry" : "Deterministic replay"}
    </span>
  );
}

function ProbeStatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`res-verdict ${ok ? "is-pass" : "is-fail"}`}>
      <span aria-hidden="true">{ok ? "✓" : "✕"}</span> {label}
    </span>
  );
}

export function ResiliencePanel({
  view,
  source,
}: {
  view: ResilienceView;
  source: ReportSource;
}) {
  const phases = useMemo(() => failoverPhases(view), [view]);
  const [phaseIndex, setPhaseIndex] = useState(0);
  const [autoplay, setAutoplay] = useState(true);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!autoplay || prefersReducedMotion()) return;
    timer.current = window.setInterval(() => {
      setPhaseIndex((index) => (index + 1) % phases.length);
    }, PHASE_INTERVAL_MS);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [autoplay, phases.length]);

  const phase = phases[phaseIndex];
  const nodes = useMemo(
    () => nodesForPhase(view, phase.regionDown),
    [view, phase.regionDown],
  );

  const stopAutoplayAndSet = (index: number) => {
    setAutoplay(false);
    setPhaseIndex(index);
  };

  const rtoLabel =
    phase.rtoSeconds === null
      ? "—"
      : `${phase.rtoSeconds < 1 ? phase.rtoSeconds.toFixed(3) : phase.rtoSeconds.toFixed(2)}s`;

  return (
    <section className="res-panel" aria-labelledby="res-heading">
      <header className="res-panel__head">
        <div>
          <span className="eyebrow">Production readiness · Track A</span>
          <h1 id="res-heading">Failover theater</h1>
          <p className="res-lede">
            We kill the primary region live. Recovery is under target, and the
            memory the agent wrote seconds earlier survives — read from a
            surviving replica.
          </p>
        </div>
        <div className="res-head-meta">
          <SourceChip source={source} />
          <ProbeStatusDot ok={view.overall.pass} label={view.overall.pass ? "All probes pass" : "Probe failed"} />
        </div>
      </header>

      <div className="res-stage">
        {/* The money shot: RPO pinned to 0 through every phase. */}
        <div className={`res-rpo res-rpo--${phase.id}`} aria-live="off">
          <span className="eyebrow">Recovery point objective</span>
          <strong className="res-rpo__value">
            {phase.rpoRowsLost ?? "—"}
          </strong>
          <span className="res-rpo__unit">rows lost</span>
          <span className="res-rpo__hold">
            held through the kill · {view.rpo.rowsFound}/{view.rpo.rowsExpected} tracked rows re-read intact
          </span>
        </div>

        <div className="res-outage">
          <div className="res-outage__top">
            <span className="res-phase-label">{phase.label}</span>
            <span className="res-liveness" aria-label="Live nodes">
              <strong>{phase.liveNodes}</strong>
              <span>/ {view.liveness.expected} nodes live</span>
            </span>
          </div>

          <div className="res-regions" role="img" aria-label={`Cluster nodes, ${phase.label}`}>
            {view.regions.map((region) => {
              const regionNodes = nodes.filter((node) => node.region === region);
              const killed = phase.regionDown && region === view.killedRegion;
              return (
                <div
                  className={`res-region ${killed ? "is-down" : "is-up"} ${region === view.primaryRegion ? "is-primary" : ""}`}
                  key={region}
                >
                  <span className="res-region__name">
                    {region}
                    {region === view.primaryRegion ? <em> primary</em> : null}
                  </span>
                  <div className="res-region__nodes">
                    {regionNodes.map((node) => (
                      <span
                        key={node.id}
                        className={`res-node ${node.down ? "is-down" : "is-up"}`}
                        title={`${node.id}${node.down ? " · down" : " · live"}`}
                      >
                        <span className="res-node__zone">{node.zone}</span>
                      </span>
                    ))}
                  </div>
                  {killed ? <span className="res-region__badge">REGION DOWN</span> : null}
                </div>
              );
            })}
          </div>

          <p className="res-caption">{phase.caption}</p>

          <div className="res-scrubber" role="group" aria-label="Failover timeline">
            {phases.map((item, index) => (
              <button
                key={item.id}
                type="button"
                className={`res-step ${index === phaseIndex ? "is-active" : ""} ${item.regionDown ? "is-kill" : ""}`}
                aria-pressed={index === phaseIndex}
                onClick={() => stopAutoplayAndSet(index)}
              >
                <span className="res-step__dot" aria-hidden="true" />
                {item.label}
              </button>
            ))}
            <button
              type="button"
              className="res-step res-step--toggle"
              onClick={() => setAutoplay((value) => !value)}
            >
              {autoplay ? "Pause" : "Play"}
            </button>
          </div>
        </div>

        <div className="res-rto">
          <span className="eyebrow">Recovery time objective</span>
          <strong className={`res-rto__value ${phase.rtoSeconds === null ? "is-pending" : ""}`}>
            {rtoLabel}
          </strong>
          <span className="res-rto__target">target &lt; {view.rto.targetSeconds}s</span>
          {view.rto.recoveredViaRegion ? (
            <span className="res-rto__via">
              write availability restored via <strong>{view.rto.recoveredViaRegion}</strong>
            </span>
          ) : null}
        </div>
      </div>

      <div className="res-wedges">
        <article className="res-wedge">
          <div className="res-wedge__head">
            <span className="eyebrow">Read-your-own-writes</span>
            <ProbeStatusDot
              ok={view.freshness.status === "pass"}
              label={view.freshness.foundImmediately ? "found immediately" : "replication lag"}
            />
          </div>
          <p className="res-chip res-chip--gold">
            <span aria-hidden="true">✦</span> read latency
            <strong>{view.freshness.staleMs !== null ? `${view.freshness.staleMs} ms` : "—"}</strong>
          </p>
          <p className="res-wedge__note">
            wrote on <code>{view.freshness.writeNode}</code>, read back on{" "}
            <code>{view.freshness.readNode}</code> — no replication-lag window to wait out.
          </p>
        </article>

        <article className="res-wedge">
          <div className="res-wedge__head">
            <span className="eyebrow">Single-store atomicity</span>
            <ProbeStatusDot ok={view.atomicity.status === "pass"} label="commit or abort together" />
          </div>
          <div className="res-envelope">
            <code className="res-envelope__begin">BEGIN;</code>
            <ol>
              <li className="res-stmt res-stmt--memory">
                <span aria-hidden="true">●</span> memory
                <code>INSERT episodic_events</code>
                <span className={view.atomicity.commitPass ? "is-present" : "is-absent"}>present</span>
              </li>
              <li className="res-stmt res-stmt--action">
                <span aria-hidden="true">●</span> action
                <code>INSERT remediation_actions</code>
                <span className={view.atomicity.commitPass ? "is-present" : "is-absent"}>present</span>
              </li>
            </ol>
            <code className="res-envelope__commit">COMMIT ✓</code>
            <p className="res-envelope__abort">
              abort path: the CHECK violation rolled back{" "}
              <strong>both</strong> writes together —{" "}
              {view.atomicity.constraintViolationRaised ? "0 dangling rows" : "not exercised"}.
            </p>
          </div>
        </article>

        <article className="res-wedge">
          <div className="res-wedge__head">
            <span className="eyebrow">Cross-agent visibility</span>
            <ProbeStatusDot ok={view.crossAgent.status === "pass"} label="no lag" />
          </div>
          <div className="res-cross">
            <span className="res-cross__node">
              <em>write</em> {view.crossAgent.writerRegion}
            </span>
            <span className="res-cross__arrow" aria-hidden="true">→</span>
            <span className="res-cross__node">
              <em>read</em> {view.crossAgent.readerRegion}
            </span>
          </div>
          <p className="res-chip res-chip--cyan">
            <span aria-hidden="true">✓</span>
            {view.crossAgent.crossRegion ? "cross-region" : "same-region"} read
            <strong>
              {view.crossAgent.latencyMs !== null ? `${view.crossAgent.latencyMs} ms` : "—"}
            </strong>
          </p>
          <p className="res-wedge__note">
            One agent writes through one gateway; another reads through a
            different region and sees it instantly.
          </p>
        </article>
      </div>
    </section>
  );
}
