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
    assert rpo["details"]["phase"] == "after_recovery"
    assert rpo["details"]["rows_missing"] == []
    assert rpo["details"]["rows_content_mismatched"] == []
    assert rpo["measured_value"] == 0.0
    # Charter R5: content verification must be live, not dead code -- every
    # tracked row must actually have been content-checked, not merely
    # existence-checked.
    assert rpo["details"]["rows_content_checked"] == rpo["details"]["rows_expected"] > 0


def test_rpo_is_zero_rows_lost_during_the_outage(report: dict) -> None:
    """Charter R5: "verify data during the outage", not only after full
    recovery. This is the check that runs while the killed region's nodes
    are still down (before restore_region is called)."""
    rpo_during = report["probes"]["rpo_during_outage"]
    assert rpo_during["status"] == "pass", rpo_during["details"]
    assert rpo_during["details"]["phase"] == "during_outage"
    assert rpo_during["details"]["rows_missing"] == []
    assert rpo_during["details"]["rows_content_mismatched"] == []
    assert rpo_during["measured_value"] == 0.0
    assert rpo_during["details"]["rows_content_checked"] == rpo_during["details"]["rows_expected"] > 0


def test_leaseholders_were_pinned_into_the_kill_region_before_the_kill(report: dict) -> None:
    """The mechanical fix for the audit finding: the killed region must
    actually own the leaseholders of the tables this proof measures BEFORE
    the kill, not zero of them."""
    pin = report["leaseholder_pin"]
    killed_region = report["topology"]["killed_region"]
    for table in ("episodic_events", "remediation_actions"):
        assert table in pin, pin
        assert pin[table]["verified"] is True, pin[table]
        assert pin[table]["target_region"] == killed_region
        assert pin[table]["ranges_total"] > 0
        assert pin[table]["ranges_in_region"] == pin[table]["ranges_total"], pin[table]

    # And the pre-kill range snapshot must show it too, independently --
    # this is exactly the counter the original audit caught reading
    # {"us-east-1": 8, "us-west-2": 1} (zero leaseholders in the killed
    # region) despite killing us-east-2.
    before = report["range_snapshot"]["before_kill"]["leaseholder_region_counts"]
    assert before.get(killed_region, 0) > 0, before
    assert before.get(killed_region, 0) == report["range_snapshot"]["before_kill"]["range_count"]


def test_rto_is_under_target(report: dict) -> None:
    rto = report["probes"]["rto"]
    assert rto["status"] == "pass", rto["details"]
    assert rto["measured_value"] is not None
    assert rto["measured_value"] < rto["details"]["target_seconds"]


def test_rto_recorded_a_real_failover_not_a_normal_write(report: dict) -> None:
    """Charter R5: a pass must mean a real lease transfer happened. The RTO
    probe must have recorded at least one failed write attempt against the
    killed region's (pinned) leaseholder before its first success -- proving
    the write path actually went through the dead leaseholder, not around
    it. This is the direct regression test for the original audit finding
    (killed region owned zero leaseholders, so "RTO" was a normal write to
    an untouched node)."""
    rto = report["probes"]["rto"]
    details = rto["details"]
    assert details["failover_exercised"] is True, details
    assert details["kill_region_failed_attempts"] >= 1, details
    assert details["leaseholder_region"] == report["topology"]["killed_region"]

    kill_region_failures = [
        a for a in details["attempts"]
        if a["region"] == report["topology"]["killed_region"] and a["ok"] is False
    ]
    assert len(kill_region_failures) >= 1, details["attempts"]
    # The very first attempt in the log must be the guaranteed-dead
    # leaseholder attempt, not a lucky later one.
    assert details["attempts"][0]["ok"] is False
    assert details["attempts"][0]["region"] == report["topology"]["killed_region"]
    assert details["attempts"][0]["node"] == details["leaseholder_node"]


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
