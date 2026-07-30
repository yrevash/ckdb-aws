"""The real proof: runs the full ResilienceHarness end to end against
docker-compose.multiregion.yml, including an actual `docker compose kill` of
an entire region's 3 nodes and the restore afterward. This is the same code
path scripts/measure_resilience.sh's CLI invocation uses -- one region-kill
implementation, exercised here as an assertable pytest test and there as the
JSON-report-producing entry point.

Skipped automatically if the cluster isn't up (see tests/_live.py). Takes
on the order of 10-60 seconds (region kill -> RTO measurement -> restore ->
wait for full node liveness) -- this is the expensive test in this package,
by design.
"""

from __future__ import annotations

import pytest

from postmortem_resilience.harness import HarnessConfig, ResilienceHarness

from ._live import CLUSTER_REACHABLE, SKIP_REASON

pytestmark = pytest.mark.skipif(not CLUSTER_REACHABLE, reason=SKIP_REASON)


@pytest.fixture(scope="module")
def report() -> dict:
    harness = ResilienceHarness(HarnessConfig())
    return harness.run()


def test_rpo_is_zero_rows_lost(report: dict) -> None:
    rpo = report["probes"]["rpo"]
    assert rpo["status"] == "pass", rpo["details"]
    assert rpo["details"]["rows_missing"] == []
    assert rpo["details"]["rows_content_mismatched"] == []
    assert rpo["measured_value"] == 0.0


def test_rto_is_under_target(report: dict) -> None:
    rto = report["probes"]["rto"]
    assert rto["status"] == "pass", rto["details"]
    assert rto["measured_value"] is not None
    assert rto["measured_value"] < rto["details"]["target_seconds"]


def test_freshness_and_cross_agent_and_atomicity_all_pass(report: dict) -> None:
    assert report["probes"]["freshness"]["status"] == "pass"
    assert report["probes"]["cross_agent_visibility"]["status"] == "pass"
    assert report["probes"]["atomicity"]["status"] == "pass"


def test_node_liveness_dropped_during_outage_and_recovered(report: dict) -> None:
    liveness = report["node_liveness"]
    assert liveness["before_kill"] == liveness["expected"] == 9
    # The harness holds (region_control.wait_for_region_down) until the
    # killed region's gossip-level liveness record actually flips, bounded
    # by HarnessConfig.region_down_wait_seconds -- assert on that flag
    # rather than a bare node count, since a timeout is still an honestly
    # reported (if unexpected) outcome, not a harness bug.
    assert liveness["region_down_detected"] is True, liveness
    assert liveness["during_outage"] == 6, "expected exactly 3 nodes (one region) to drop"
    assert liveness["after_recovery"] == 9
    assert liveness["recovery_reached_full_liveness"] is True


def test_overall_report_passes(report: dict) -> None:
    assert report["overall"]["pass"] is True, report["overall"]
