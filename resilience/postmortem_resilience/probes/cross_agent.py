"""Cross-agent memory visibility probe.

Charter §8: "no lag" between an agent instance writing a memory through one
gateway node/region and a *different* agent instance reading it through
another. This is functionally the same mechanism as the freshness probe
(probes/freshness.py) -- CockroachDB has one visibility model regardless of
which node a client happens to connect through -- but it is measured and
reported separately because it is a distinct charter claim (multi-agent
consistency, not single-agent read-your-write), and because the two probes
are deliberately run against nodes in *different regions* here to make the
"no special-casing for the gateway node" property concrete.
"""

from __future__ import annotations

from uuid import uuid4

from .. import db
from ..report import ProbeResult
from ..seed import SeedContext
from ..topology import Node


def probe_cross_agent_visibility(*, writer_node: Node, reader_node: Node,
                                  seed: SeedContext) -> ProbeResult:
    event_id = str(uuid4())
    marker = f"resilience-cross-agent-{event_id}"
    writer_agent_id = seed.agent_id
    reader_agent_id = seed.agent_id  # same logical agent identity, different node/gateway

    write_conn = db.connect(writer_node)
    try:
        with write_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO episodic_events (
                    event_id, org_id, agent_id, incident_id, service_id,
                    event_type, content, metadata
                )
                VALUES (%s, %s, %s, %s, %s, 'observation', %s, %s)
                """,
                (event_id, seed.org_id, writer_agent_id, seed.incident_id,
                 seed.service_id, marker,
                 '{"written_via_node": "%s", "written_via_region": "%s"}'
                 % (writer_node.service, writer_node.region)),
            )
        write_conn.commit()
    finally:
        write_conn.close()

    def read() -> tuple[bool, str | None]:
        read_conn = db.connect(reader_node)
        try:
            with read_conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM episodic_events "
                    "WHERE org_id = %s AND agent_id = %s AND event_id = %s",
                    (seed.org_id, reader_agent_id, event_id),
                )
                row = cur.fetchone()
                return (row is not None, row[0] if row else None)
        finally:
            read_conn.close()

    timed = db.timed(read)
    found, content = timed.value
    status = "pass" if found and content == marker else "fail"

    return ProbeResult(
        probe_type="cross_agent_visibility",
        status=status,
        measured_value=round(timed.elapsed_ms, 3),
        unit="ms",
        details={
            "event_id": event_id,
            "content": marker,
            "writer_node": writer_node.service,
            "writer_region": writer_node.region,
            "reader_node": reader_node.service,
            "reader_region": reader_node.region,
            "cross_region": writer_node.region != reader_node.region,
            "found_immediately": found,
            "content_matches": content == marker,
        },
    )
