"""RTO probe: wall-clock time from a region kill to restored write
availability, target < 10 seconds (charter §8 / PHASE3_PLAN.md Track A).

Charter R5: a failover proof must exercise the actual failure it claims. It
is not enough for *some* write somewhere to eventually succeed after the
kill -- that is true even if the killed region never held a single
leaseholder (the original audit finding). This probe requires the *first*
attempt to be aimed at `leaseholder_node` -- the exact node that held the
probed table's leaseholder immediately before the kill, per
`leaseholders.pin_and_verify` -- so the very first attempt is guaranteed to
hit the dead leaseholder and fail. Only after that confirmed failure does it
fall back to a rotating list of every other node in the cluster (modeling a
real client behind a load balancer that doesn't know which nodes just died),
with a short connect timeout so attempts against dead nodes fail fast (TCP
connection refused). If no attempt against a node in the killed region is
ever recorded as failed, the probe fails outright as "no failover exercised"
regardless of whether some write eventually succeeded -- a pass here must
mean a real lease transfer happened.
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


def _attempt_write(node: Node, seed: SeedContext) -> tuple[str, str]:
    """Single write attempt against `node`. Raises on any failure. Returns
    (event_id, content) written on success."""
    event_id = str(uuid4())
    content = f"resilience-rto-probe-{event_id}"
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
                 seed.service_id, content),
            )
        conn.commit()
        return event_id, content
    finally:
        conn.close()


def probe_rto(*, kill_started_at: float, candidate_nodes: tuple[Node, ...],
              seed: SeedContext, kill_region: str, leaseholder_node: Node,
              target_seconds: float = 10.0,
              deadline_seconds: float = 60.0, tracker=None) -> ProbeResult:
    """`leaseholder_node` must be the exact node that held the probed
    table's leaseholder immediately before the kill (see
    `leaseholders.pin_and_verify`); it is always tried first so the first
    attempt is guaranteed to hit the dead leaseholder. `candidate_nodes`
    (typically the full cluster topology) is the rotation used afterward."""
    attempts_log: list[dict[str, object]] = []
    ordered_nodes = [leaseholder_node] + [
        n for n in candidate_nodes if n.service != leaseholder_node.service
    ]
    rotation = itertools.cycle(ordered_nodes)

    first_success_at: float | None = None
    first_success_event_id: str | None = None
    first_success_node: Node | None = None
    kill_region_failed_attempts = 0

    while time.time() - kill_started_at < deadline_seconds:
        node = next(rotation)
        attempt_start = time.time()
        try:
            event_id, content = _attempt_write(node, seed)
        except Exception as exc:  # noqa: BLE001 - expected for the killed region's nodes
            attempts_log.append({
                "node": node.service, "region": node.region, "ok": False,
                "elapsed_ms": round((time.time() - attempt_start) * 1000, 1),
                "error": type(exc).__name__,
            })
            if node.region == kill_region:
                kill_region_failed_attempts += 1
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
                          row_id=event_id, org_id=seed.org_id,
                          content_column="content", expected_content=content)
        break

    failover_exercised = kill_region_failed_attempts >= 1

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
                "leaseholder_node": leaseholder_node.service,
                "leaseholder_region": leaseholder_node.region,
                "failover_exercised": failover_exercised,
                "kill_region_failed_attempts": kill_region_failed_attempts,
                "attempts": attempts_log,
            },
        )

    if not failover_exercised:
        # The pre-kill leaseholder attempt did NOT fail -- either it wasn't
        # actually pinned to the killed region, or it somehow already
        # succeeded (e.g. the region wasn't really down). Either way this is
        # exactly the original audit finding (a write to a node that was
        # never touched by the kill) and must not be reported as a pass.
        rto_seconds = first_success_at - kill_started_at
        return ProbeResult(
            probe_type="rto",
            status="fail",
            measured_value=round(rto_seconds, 3),
            unit="seconds",
            details={
                "target_seconds": target_seconds,
                "reason": "no failover exercised: no write attempt against "
                          "the killed region's (pinned) leaseholder failed "
                          "before the first success",
                "leaseholder_node": leaseholder_node.service,
                "leaseholder_region": leaseholder_node.region,
                "failover_exercised": False,
                "kill_region_failed_attempts": kill_region_failed_attempts,
                "recovered_via_node": first_success_node.service if first_success_node else None,
                "recovered_via_region": first_success_node.region if first_success_node else None,
                "first_success_event_id": first_success_event_id,
                "attempt_count": len(attempts_log),
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
            "leaseholder_node": leaseholder_node.service,
            "leaseholder_region": leaseholder_node.region,
            "failover_exercised": failover_exercised,
            "kill_region_failed_attempts": kill_region_failed_attempts,
            "recovered_via_node": first_success_node.service if first_success_node else None,
            "recovered_via_region": first_success_node.region if first_success_node else None,
            "first_success_event_id": first_success_event_id,
            "attempt_count": len(attempts_log),
            "attempts": attempts_log,
        },
    )
