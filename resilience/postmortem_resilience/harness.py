"""Orchestrates the full Phase 3 / Track A resilience proof end to end
against the local simulated multi-region cluster: seed -> baseline probes ->
kill a region -> measure RTO through the outage -> restore -> verify RPO=0.

This is the single source of truth both `scripts/measure_resilience.sh` (via
`python -m postmortem_resilience`) and `resilience/tests/test_live_
resilience.py` call into -- there is exactly one region-kill code path, not
two independently-maintained ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from . import db, region_control
from .leaseholders import (
    DEFAULT_PIN_POLL_INTERVAL_S,
    DEFAULT_PIN_TIMEOUT_S,
    pin_and_verify,
    resolve_node_from_locality,
)
from .probes import (
    RpoTracker,
    probe_atomicity,
    probe_cross_agent_visibility,
    probe_freshness,
    probe_rpo,
    probe_rto,
)
from .ranges import range_snapshot
from .report import ProbeResult, ResilienceReport, now_iso, overall_from_probes, record_probe
from .seed import seed_baseline
from .topology import (
    CONTROL_NODE,
    DEFAULT_KILL_REGION,
    TOPOLOGY,
    Node,
    nodes_in_region,
)

# Tables the RPO tracker touches directly (episodic memory + the operational
# table the atomicity probe pairs it with). These are the tables pinned into
# the kill region before every run so the kill actually exercises their
# leaseholders -- see leaseholders.py's module docstring for why this is
# necessary and db/bootstrap/010_multiregion.sql's REGIONAL BY TABLE IN
# PRIMARY REGION default never would.
PINNED_TABLES: tuple[str, ...] = ("episodic_events", "remediation_actions")


@dataclass
class HarnessConfig:
    kill_region: str = DEFAULT_KILL_REGION
    control_node: Node = field(default_factory=lambda: CONTROL_NODE)
    target_rto_seconds: float = 10.0
    rto_deadline_seconds: float = 60.0
    region_down_wait_seconds: float = 15.0
    recovery_timeout_seconds: float = 150.0
    extra_writes_during_outage: int = 2
    pinned_tables: tuple[str, ...] = PINNED_TABLES
    leaseholder_pin_timeout_seconds: float = DEFAULT_PIN_TIMEOUT_S
    leaseholder_pin_poll_interval_seconds: float = DEFAULT_PIN_POLL_INTERVAL_S


class ResilienceHarness:
    def __init__(self, config: HarnessConfig | None = None) -> None:
        self.config = config or HarnessConfig()

    def run(self) -> dict:
        cfg = self.config
        control = cfg.control_node
        kill_region_nodes = nodes_in_region(cfg.kill_region)
        surviving_third_region_nodes = [
            n for n in TOPOLOGY if n.region not in (control.region, cfg.kill_region)
        ]
        third_region_node = surviving_third_region_nodes[0] if surviving_third_region_nodes else control

        # --- seed -----------------------------------------------------
        seed_conn = db.connect(control)
        try:
            seed = seed_baseline(seed_conn)
        finally:
            seed_conn.close()

        tracker = RpoTracker()

        # --- pin leaseholders into the kill region, BEFORE the kill (R5) --
        # db/bootstrap/010_multiregion.sql sets every table to REGIONAL BY
        # TABLE IN PRIMARY REGION, which never moves a leaseholder when a
        # non-primary region (the kill target) goes down -- that is the
        # exact bug the audit found (killed region owned zero leaseholders,
        # so the "RTO" was a normal write to an untouched node). This
        # overrides just the lease preference for the tables this harness
        # actually probes, and verifies via SHOW RANGES that the move
        # really happened before proceeding -- raises LeaseholderPinError
        # (aborting the run) rather than silently continuing to kill a
        # region that doesn't hold what this proof needs it to hold.
        leaseholder_pin = pin_and_verify(
            control, tables=cfg.pinned_tables, region=cfg.kill_region,
            timeout_s=cfg.leaseholder_pin_timeout_seconds,
            poll_interval_s=cfg.leaseholder_pin_poll_interval_seconds,
        )

        # The exact node holding episodic_events' leaseholder right now
        # (i.e. immediately pre-kill) -- probe_rto uses this to guarantee
        # its first write attempt targets the about-to-be-dead leaseholder.
        episodic_locality = leaseholder_pin["episodic_events"]["sample_leaseholder_locality"]
        leaseholder_node = resolve_node_from_locality(episodic_locality) if episodic_locality else None
        if leaseholder_node is None:
            raise RuntimeError(
                "could not resolve a concrete node from the pinned "
                f"episodic_events leaseholder locality {episodic_locality!r} -- "
                "refusing to proceed without a guaranteed real-leaseholder RTO probe."
            )
        if leaseholder_node.region != cfg.kill_region:
            raise RuntimeError(
                f"episodic_events leaseholder resolved to {leaseholder_node.service!r} "
                f"in region {leaseholder_node.region!r}, not the kill region "
                f"{cfg.kill_region!r} -- pin verification should have caught this."
            )

        # --- pre-kill baseline -----------------------------------------
        liveness_before = region_control.node_status(query_node=control)
        live_before = region_control.count_live(liveness_before)
        ranges_before = range_snapshot(control)

        freshness = probe_freshness(
            write_node=control, read_node=third_region_node, seed=seed
        )
        tracker.track(table="episodic_events", id_column="event_id",
                      row_id=freshness.details["event_id"], org_id=seed.org_id,
                      content_column="content", expected_content=freshness.details["content"])

        cross_agent = probe_cross_agent_visibility(
            writer_node=kill_region_nodes[0], reader_node=control, seed=seed
        )
        tracker.track(table="episodic_events", id_column="event_id",
                      row_id=cross_agent.details["event_id"], org_id=seed.org_id,
                      content_column="content", expected_content=cross_agent.details["content"])

        atomicity = probe_atomicity(node=control, seed=seed)
        tracker.track(table="episodic_events", id_column="event_id",
                      row_id=atomicity.details["commit_path"]["event_id"], org_id=seed.org_id,
                      content_column="content",
                      expected_content=atomicity.details["commit_path"]["event_content"])
        tracker.track(table="remediation_actions", id_column="action_id",
                      row_id=atomicity.details["commit_path"]["action_id"], org_id=seed.org_id,
                      content_column="idempotency_key",
                      expected_content=atomicity.details["commit_path"]["action_idempotency_key"])

        # --- the kill ----------------------------------------------------
        kill_started_at = region_control.kill_region(cfg.kill_region)

        rto = probe_rto(
            kill_started_at=kill_started_at,
            candidate_nodes=TOPOLOGY,
            seed=seed,
            kill_region=cfg.kill_region,
            leaseholder_node=leaseholder_node,
            target_seconds=cfg.target_rto_seconds,
            deadline_seconds=cfg.rto_deadline_seconds,
            tracker=tracker,
        )

        # Prove sustained (not just first-blip) write availability through
        # the rest of the outage window, on the surviving quorum.
        outage_writes: list[str] = []
        for _ in range(cfg.extra_writes_during_outage):
            extra_conn = db.connect(control)
            try:
                extra_event_id = str(uuid4())
                extra_content = "resilience harness: write during outage"
                with extra_conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO episodic_events (
                            event_id, org_id, agent_id, incident_id, service_id,
                            event_type, content
                        )
                        VALUES (%s, %s, %s, %s, %s, 'observation', %s)
                        """,
                        (extra_event_id, seed.org_id, seed.agent_id,
                         seed.incident_id, seed.service_id, extra_content),
                    )
                extra_conn.commit()
                outage_writes.append(extra_event_id)
                tracker.track(table="episodic_events", id_column="event_id",
                              row_id=extra_event_id, org_id=seed.org_id,
                              content_column="content", expected_content=extra_content)
            finally:
                extra_conn.close()

        # Hold here until the killed region's nodes actually flip
        # is_live=false (bounded wait) -- see wait_for_region_down's
        # docstring: write availability (RTO, measured above) typically
        # recovers *faster* than the gossip-level liveness record expires,
        # so without this the "9 -> 6 -> 9" liveness drop the demo narrates
        # would never actually be observed in a fast run.
        region_down_reached, region_down_elapsed_s, liveness_during = region_control.wait_for_region_down(
            cfg.kill_region, timeout_s=cfg.region_down_wait_seconds, query_node=control,
        )
        live_during = region_control.count_live(liveness_during)
        ranges_during = range_snapshot(control)

        # --- RPO verification DURING the outage (R5: "verify data during
        # the outage", not only after recovery) -- the killed region is
        # still down at this point (restore_region has not been called
        # yet); every row tracked so far (including the RTO probe's
        # post-failover write and the outage writes above) must already be
        # present, with matching content, on a surviving node. ------------
        rpo_during_outage = probe_rpo(node=control, tracker=tracker, phase="during_outage")

        # --- restore -------------------------------------------------
        region_control.restore_region(cfg.kill_region)
        recovered, recovery_elapsed_s, liveness_after = region_control.wait_for_full_liveness(
            expected_live=len(TOPOLOGY),
            timeout_s=cfg.recovery_timeout_seconds,
            query_node=control,
        )
        live_after = region_control.count_live(liveness_after)
        ranges_after = range_snapshot(control)

        # --- RPO verification after recovery (the canonical, final result
        # -- rows written during the outage were already durable the
        # moment they committed and already re-confirmed above; this
        # re-checks once more now that the killed region has rejoined) ----
        rpo = probe_rpo(node=control, tracker=tracker, phase="after_recovery")

        # --- persist probes into eval_probes (belt-and-suspenders: the
        # proof lives in the database, not only in the JSON file) -------
        record_conn = db.connect(control)
        try:
            for probe in (freshness, cross_agent, atomicity, rto, rpo_during_outage, rpo):
                record_probe(record_conn, seed.org_id, probe)
        finally:
            record_conn.close()

        probes_by_name: dict[str, ProbeResult] = {
            "freshness": freshness,
            "cross_agent_visibility": cross_agent,
            "atomicity": atomicity,
            "rto": rto,
            "rpo_during_outage": rpo_during_outage,
            "rpo": rpo,
        }

        report = ResilienceReport(
            generated_at=now_iso(),
            topology={
                "regions": sorted({n.region for n in TOPOLOGY}),
                "primary_region": control.region,
                "killed_region": cfg.kill_region,
                "nodes_total": len(TOPOLOGY),
                "replication_factor": 5,
            },
            run={
                "org_id": seed.org_id,
                "agent_id": seed.agent_id,
                "service_id": seed.service_id,
                "incident_id": seed.incident_id,
                "outage_writes": outage_writes,
                "rows_tracked_for_rpo": len(tracker),
            },
            probes={name: p.to_dict() for name, p in probes_by_name.items()},
            leaseholder_pin=leaseholder_pin,
            node_liveness={
                "before_kill": live_before,
                "during_outage": live_during,
                "after_recovery": live_after,
                "expected": len(TOPOLOGY),
                "region_down_detected": region_down_reached,
                "region_down_detection_seconds": round(region_down_elapsed_s, 2),
                "recovery_reached_full_liveness": recovered,
                "recovery_elapsed_seconds": round(recovery_elapsed_s, 2),
            },
            range_snapshot={
                "before_kill": ranges_before,
                "during_outage": ranges_during,
                "after_recovery": ranges_after,
            },
            overall=overall_from_probes(probes_by_name),
        )
        return report.to_dict()


def run_and_write(output_path: str) -> dict:
    """Run the full harness once and write the resulting report dict as JSON
    to `output_path`. Used by both the CLI (`__main__.py`) and any test that
    wants the on-disk artifact, not just the in-memory dict."""
    import json
    from pathlib import Path

    harness = ResilienceHarness()
    report_dict = harness.run()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(report_dict, indent=2, sort_keys=True) + "\n")
    return report_dict
