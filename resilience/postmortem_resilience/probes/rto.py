"""RTO probe: wall-clock time from a region kill to restored write
availability, target < 10 seconds (charter §8 / PHASE3_PLAN.md Track A).

Models a real client behind a load balancer that doesn't know which nodes
just died: it keeps a rotating list of every node in the cluster (dead and
alive) and, on each attempt, tries the next one with a short connect timeout.
Attempts against the killed region's nodes fail fast (TCP connection
refused, since the process is gone) so the loop naturally converges onto a
surviving node within a few attempts -- exactly the RTO wall-clock the
charter asks to prove.
"""

from __future__ import annotations

import itertools
import time
from uuid import uuid4

from .. import db
from ..report import ProbeResult
from ..seed import SeedContext
from ..topology import Node

RETRY_CONNECT_TIMEOUT_S = 1.0
RETRY_SLEEP_BETWEEN_ATTEMPTS_S = 0.1


def _attempt_write(node: Node, seed: SeedContext) -> str:
    """Single write attempt against `node`. Raises on any failure. Returns
    the event_id written on success."""
    event_id = str(uuid4())
    conn = db.connect(node, connect_timeout=RETRY_CONNECT_TIMEOUT_S)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO episodic_events (
                    event_id, org_id, agent_id, incident_id, service_id,
                    event_type, content
                )
                VALUES (%s, %s, %s, %s, %s, 'observation', %s)
                """,
                (event_id, seed.org_id, seed.agent_id, seed.incident_id,
                 seed.service_id, f"resilience-rto-probe-{event_id}"),
            )
        conn.commit()
        return event_id
    finally:
        conn.close()


def probe_rto(*, kill_started_at: float, candidate_nodes: tuple[Node, ...],
              seed: SeedContext, target_seconds: float = 10.0,
              deadline_seconds: float = 60.0, tracker=None) -> ProbeResult:
    attempts_log: list[dict[str, object]] = []
    rotation = itertools.cycle(candidate_nodes)

    first_success_at: float | None = None
    first_success_event_id: str | None = None
    first_success_node: Node | None = None

    while time.time() - kill_started_at < deadline_seconds:
        node = next(rotation)
        attempt_start = time.time()
        try:
            event_id = _attempt_write(node, seed)
        except Exception as exc:  # noqa: BLE001 - expected for the killed region's nodes
            attempts_log.append({
                "node": node.service, "region": node.region, "ok": False,
                "elapsed_ms": round((time.time() - attempt_start) * 1000, 1),
                "error": type(exc).__name__,
            })
            continue

        attempts_log.append({
            "node": node.service, "region": node.region, "ok": True,
            "elapsed_ms": round((time.time() - attempt_start) * 1000, 1),
        })
        first_success_at = time.time()
        first_success_event_id = event_id
        first_success_node = node
        if tracker is not None:
            tracker.track(table="episodic_events", id_column="event_id",
                          row_id=event_id, org_id=seed.org_id)
        break

    if first_success_at is None:
        return ProbeResult(
            probe_type="rto",
            status="fail",
            measured_value=None,
            unit="seconds",
            details={
                "target_seconds": target_seconds,
                "deadline_seconds": deadline_seconds,
                "reason": "no successful write within deadline",
                "attempts": attempts_log,
            },
        )

    rto_seconds = first_success_at - kill_started_at
    status = "pass" if rto_seconds < target_seconds else "fail"

    return ProbeResult(
        probe_type="rto",
        status=status,
        measured_value=round(rto_seconds, 3),
        unit="seconds",
        details={
            "target_seconds": target_seconds,
            "recovered_via_node": first_success_node.service if first_success_node else None,
            "recovered_via_region": first_success_node.region if first_success_node else None,
            "first_success_event_id": first_success_event_id,
            "attempt_count": len(attempts_log),
            "attempts": attempts_log,
        },
    )
