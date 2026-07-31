"""Pure unit tests for postmortem_resilience.leaseholders -- no live cluster
required. Covers the locality-parsing helper used to resolve the exact
pre-kill leaseholder node for the RTO probe (resilience/postmortem_
resilience/harness.py) and the pin/verify result shape.
"""

from __future__ import annotations

from postmortem_resilience.leaseholders import (
    LeaseholderVerification,
    resolve_node_from_locality,
)
from postmortem_resilience.topology import TOPOLOGY


def test_resolve_node_from_locality_matches_region_and_zone() -> None:
    node = resolve_node_from_locality("region=us-east-2,zone=b")
    assert node is not None
    assert node.region == "us-east-2"
    assert node.zone == "b"
    assert node.service == "crdb-use2-b"


def test_resolve_node_from_locality_covers_every_topology_node() -> None:
    for node in TOPOLOGY:
        locality = f"region={node.region},zone={node.zone}"
        resolved = resolve_node_from_locality(locality)
        assert resolved is node


def test_resolve_node_from_locality_returns_none_for_garbage() -> None:
    assert resolve_node_from_locality("") is None
    assert resolve_node_from_locality("not-a-locality-string") is None


def test_leaseholder_verification_to_dict_round_trips() -> None:
    v = LeaseholderVerification(
        table="episodic_events",
        target_region="us-east-2",
        verified=True,
        elapsed_seconds=12.345,
        ranges_total=3,
        ranges_in_region=3,
        leaseholder_store_ids=(2, 7, 8),
        sample_leaseholder_locality="region=us-east-2,zone=a",
    )
    out = v.to_dict()
    assert out["table"] == "episodic_events"
    assert out["verified"] is True
    assert out["elapsed_seconds"] == 12.35  # rounded
    assert out["leaseholder_store_ids"] == [2, 7, 8]
