"""Kill / restore a region's 3 nodes, and read cluster-wide node liveness.

Uses `docker compose` against docker-compose.multiregion.yml exclusively --
this module never touches the Phase 1/2 single-node cluster (docker-
compose.yml), which has no service names in common with this cluster's
`crdb-*` services.

`docker compose kill` sends SIGKILL by default: a real, hard, no-graceful-
shutdown process kill, not a drain. That is the honest simulation of "a
region just disappeared" the charter's demo thesis calls for (see
research/postmortem/04-cockroachdb-deployment-resilience.md §2.1's Plan B).
`docker compose start` restarts the *same* stopped containers against their
existing data volumes -- the nodes rejoin with their original node IDs, not
as fresh replacements, matching a real "the region came back" recovery.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .topology import COMPOSE_FILE, CONTROL_NODE, TOPOLOGY, nodes_in_region, services_in_region

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose_file_path() -> Path:
    return REPO_ROOT / COMPOSE_FILE


def _run_compose(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(_compose_file_path()), *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)


def kill_region(region: str) -> float:
    """SIGKILL every node in `region`. Returns the wall-clock time (from
    time.time()) at which the kill command returned -- the harness's RTO
    clock starts here."""
    services = services_in_region(region)
    if not services:
        raise ValueError(f"no nodes configured for region {region!r}")
    _run_compose("kill", *services)
    return time.time()


def restore_region(region: str) -> float:
    """Restart the same (previously killed) containers for `region`."""
    services = services_in_region(region)
    if not services:
        raise ValueError(f"no nodes configured for region {region!r}")
    _run_compose("start", *services)
    return time.time()


@dataclass(frozen=True)
class NodeLiveness:
    node_id: int
    address: str
    locality: str
    is_available: bool
    is_live: bool

    @property
    def region(self) -> str:
        for part in self.locality.split(","):
            if part.startswith("region="):
                return part.split("=", 1)[1]
        return ""


def _parse_node_status_tsv(output: str) -> list[NodeLiveness]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}
    rows: list[NodeLiveness] = []
    for line in lines[1:]:
        cols = line.split("\t")
        rows.append(
            NodeLiveness(
                node_id=int(cols[idx["id"]]),
                address=cols[idx["address"]],
                locality=cols[idx["locality"]],
                is_available=cols[idx["is_available"]].strip().lower() == "true",
                is_live=cols[idx["is_live"]].strip().lower() == "true",
            )
        )
    return rows


def node_status(*, query_node=CONTROL_NODE, timeout: float = 15.0) -> list[NodeLiveness]:
    """`cockroach node status` against the cluster, executed inside
    `query_node`'s container (so this works whether or not a `cockroach` CLI
    is installed on the host). `query_node` must be alive -- callers should
    always pass a node outside the region under test."""
    result = _run_compose(
        "exec", "-T", query_node.service,
        "cockroach", "node", "status", "--insecure",
        f"--host={query_node.service}:26257", "--format=tsv",
        timeout=timeout,
    )
    return _parse_node_status_tsv(result.stdout)


def count_live(rows: list[NodeLiveness]) -> int:
    return sum(1 for r in rows if r.is_live)


def wait_for_region_down(region: str, *, timeout_s: float = 15.0, poll_interval_s: float = 0.5,
                          query_node=CONTROL_NODE,
                          expected_total: int = len(TOPOLOGY)) -> tuple[bool, float, list[NodeLiveness]]:
    """Poll until the cluster reports a *stable, self-consistent* down
    state for `region` -- every node in `region` is_live=false AND every
    other node is_live=true -- or `timeout_s` elapses (returning the last
    observed state either way).

    Note this is a genuinely useful thing to observe, not a formality: write
    availability recovers (see probes/rto.py) the instant a client's
    connection to a killed node is refused -- which happens immediately on
    SIGKILL, well before CockroachDB's own gossip-level node-liveness record
    expires (node liveness has its own heartbeat/expiration cadence,
    independent of Raft-level range availability). In a fast RTO run the
    harness can finish its write-availability proof in well under a second,
    before `is_live` has even flipped. Holding here until the liveness flag
    actually drops is what makes the "9 -> 6 -> 9" liveness-count story (the
    on-camera proof from research/postmortem/04-cockroachdb-deployment-
    resilience.md §2.3) actually observable, rather than measuring so fast it
    never shows up.

    The self-consistency requirement (not just "the killed region is down")
    matters live: verified against the real cluster that gossip briefly
    reports a globally noisy snapshot in the ~1-4s right after a SIGKILL
    (observed as low as 1/9 nodes live for a single poll) before converging
    on the correct 6/9 steady state a few seconds later. Stopping on the
    first noisy poll would report a wrong, flickering liveness count instead
    of the real one.
    """
    start = time.time()
    rows: list[NodeLiveness] = []
    expected_survivors = expected_total - len(nodes_in_region(region))
    while time.time() - start < timeout_s:
        try:
            rows = node_status(query_node=query_node)
        except subprocess.CalledProcessError:
            rows = []
        region_rows = [r for r in rows if r.region == region]
        other_rows = [r for r in rows if r.region != region]
        region_down = bool(region_rows) and all(not r.is_live for r in region_rows)
        others_stable = len(other_rows) == expected_survivors and all(r.is_live for r in other_rows)
        if region_down and others_stable:
            return True, time.time() - start, rows
        time.sleep(poll_interval_s)
    return False, time.time() - start, rows


def wait_for_full_liveness(*, expected_live: int = len(TOPOLOGY),
                            timeout_s: float = 120.0, poll_interval_s: float = 2.0,
                            query_node=CONTROL_NODE) -> tuple[bool, float, list[NodeLiveness]]:
    """Poll `cockroach node status` until `expected_live` nodes report
    is_live=true, or `timeout_s` elapses. Returns (reached, elapsed_s, rows)."""
    start = time.time()
    rows: list[NodeLiveness] = []
    while time.time() - start < timeout_s:
        try:
            rows = node_status(query_node=query_node)
        except subprocess.CalledProcessError:
            rows = []
        if count_live(rows) >= expected_live:
            return True, time.time() - start, rows
        time.sleep(poll_interval_s)
    return False, time.time() - start, rows
