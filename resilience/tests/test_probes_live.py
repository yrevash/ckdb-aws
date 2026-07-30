"""Live-cluster tests for the individual probes, run WITHOUT killing any
region -- fast (a few seconds), good for iterating on probe logic without
paying the full region-kill/recovery cycle every time.
scripts/verify_phase3.sh runs this file (and test_live_resilience.py, which
does perform the real kill) against docker-compose.multiregion.yml.
"""

from __future__ import annotations

import pytest

from postmortem_resilience import db
from postmortem_resilience.probes import (
    probe_atomicity,
    probe_cross_agent_visibility,
    probe_freshness,
)
from postmortem_resilience.seed import seed_baseline
from postmortem_resilience.topology import CONTROL_NODE, TOPOLOGY, nodes_in_region

from ._live import CLUSTER_REACHABLE, SKIP_REASON

pytestmark = pytest.mark.skipif(not CLUSTER_REACHABLE, reason=SKIP_REASON)


@pytest.fixture(scope="module")
def seed():
    conn = db.connect(CONTROL_NODE)
    try:
        return seed_baseline(conn)
    finally:
        conn.close()


def test_freshness_probe_passes_with_zero_visible_staleness(seed) -> None:
    other_region_nodes = [n for n in TOPOLOGY if n.region != CONTROL_NODE.region]
    result = probe_freshness(write_node=CONTROL_NODE, read_node=other_region_nodes[0], seed=seed)
    assert result.status == "pass"
    assert result.details["found_immediately"] is True
    assert result.details["content_matches"] is True
    # Round trip latency on a local docker network should be well under a
    # second -- generous bound to avoid CI flakiness, not a tight SLA.
    assert result.measured_value < 2000


def test_cross_agent_visibility_probe_passes_across_regions(seed) -> None:
    writer = nodes_in_region("us-east-2")[0]
    reader = CONTROL_NODE
    result = probe_cross_agent_visibility(writer_node=writer, reader_node=reader, seed=seed)
    assert result.status == "pass"
    assert result.details["cross_region"] is True
    assert result.details["found_immediately"] is True


def test_atomicity_probe_commit_and_abort_paths(seed) -> None:
    result = probe_atomicity(node=CONTROL_NODE, seed=seed)
    assert result.status == "pass", result.details
    assert result.details["commit_path"]["pass"] is True
    assert result.details["abort_path"]["pass"] is True
    assert result.details["abort_path"]["constraint_violation_raised"] is True
    assert result.details["abort_path"]["episodic_present"] is False
    assert result.details["abort_path"]["action_present"] is False
