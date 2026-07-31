"""Read-your-own-writes freshness probe.

Charter §8 / PHASE3_PLAN.md Track A: "staleness == 0 ms on the leaseholder
path after commit." CockroachDB's default (non-follower, non-AS-OF-SYSTEM-
TIME) reads are always routed to the leaseholder and are linearizable with
respect to any transaction that already returned a commit ack -- there is no
replication-lag window to wait out, unlike eventually-consistent stores. This
probe demonstrates that directly: write on one connection, commit, then read
back on a second, independent connection immediately, and confirm the row is
already visible. The reported `staleness_ms` is the wall-clock cost of that
second round trip itself (network + query planning), not a "wait for it to
appear" delay -- because there is no such delay to measure.
"""

from __future__ import annotations

from uuid import uuid4

from .. import db
from ..report import ProbeResult
from ..seed import SeedContext
from ..topology import Node


def probe_freshness(*, write_node: Node, read_node: Node, seed: SeedContext) -> ProbeResult:
    event_id = str(uuid4())
    marker = f"resilience-freshness-{event_id}"

    write_conn = db.connect(write_node)
    try:
        with write_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO episodic_events (
                    event_id, org_id, agent_id, incident_id, service_id,
                    event_type, content
                )
                VALUES (%s, %s, %s, %s, %s, 'observation', %s)
                """,
                (event_id, seed.org_id, seed.agent_id, seed.incident_id,
                 seed.service_id, marker),
            )
        write_conn.commit()
    finally:
        write_conn.close()

    def read() -> tuple[bool, str | None]:
        read_conn = db.connect(read_node)
        try:
            with read_conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM episodic_events WHERE org_id = %s AND event_id = %s",
                    (seed.org_id, event_id),
                )
                row = cur.fetchone()
                return (row is not None, row[0] if row else None)
        finally:
            read_conn.close()

    timed = db.timed(read)
    found, content = timed.value
    status = "pass" if found and content == marker else "fail"

    return ProbeResult(
        probe_type="read_your_write",
        status=status,
        measured_value=round(timed.elapsed_ms, 3),
        unit="ms",
        details={
            "event_id": event_id,
            "content": marker,
            "write_node": write_node.service,
            "read_node": read_node.service,
            "same_node": write_node.service == read_node.service,
            "found_immediately": found,
            "content_matches": content == marker,
        },
    )
