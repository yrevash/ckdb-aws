"""Single-store atomicity probe: memory write + operational-mutation write,
commit-or-abort together.

Charter §4 wedge #1 and PHASE3_PLAN.md Track A: the agent's `remediate_and_
record`-style action (see db/queries/rollback_and_record.sql for the shipped
production pattern) must not be able to leave a dangling episodic memory
write with no corresponding operational action, or vice versa. This probe
exercises exactly that failure mode directly against one CockroachDB
transaction (not the full rollback_and_record CTE, which needs a live
service/deploy graph -- this probe isolates just the two tables the charter's
wedge claim is actually about: episodic_events and remediation_actions) on
both the success path and a deliberately-forced failure path, and proves
both halves commit together or both halves roll back together.
"""

from __future__ import annotations

from uuid import uuid4

from .. import db
from ..report import ProbeResult
from ..seed import SeedContext
from ..topology import Node


def _row_exists(conn, table: str, id_column: str, org_id: str, row_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {table} WHERE org_id = %s AND {id_column} = %s",  # noqa: S608 - fixed table/col set below
            (org_id, row_id),
        )
        return cur.fetchone() is not None


def probe_atomicity(*, node: Node, seed: SeedContext) -> ProbeResult:
    commit_event_id = str(uuid4())
    commit_action_id = str(uuid4())
    commit_idempotency_key = f"resilience-atomicity-commit-{commit_action_id}"

    abort_event_id = str(uuid4())
    abort_action_id = str(uuid4())

    conn = db.connect(node, autocommit=False)
    try:
        # --- commit path: both writes succeed together -----------------
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO episodic_events (
                        event_id, org_id, agent_id, incident_id, service_id,
                        event_type, content
                    )
                    VALUES (%s, %s, %s, %s, %s, 'action', 'atomicity probe: commit path')
                    """,
                    (commit_event_id, seed.org_id, seed.agent_id,
                     seed.incident_id, seed.service_id),
                )
                cur.execute(
                    """
                    INSERT INTO remediation_actions (
                        action_id, org_id, incident_id, action_type, target_id,
                        applied_by, outcome, memory_ref, idempotency_key
                    )
                    VALUES (%s, %s, %s, 'restart_service', %s, 'resilience-harness',
                            'success', %s, %s)
                    """,
                    (commit_action_id, seed.org_id, seed.incident_id,
                     seed.service_id, commit_event_id, commit_idempotency_key),
                )
        commit_episodic_present = _row_exists(conn, "episodic_events", "event_id", seed.org_id, commit_event_id)
        commit_action_present = _row_exists(conn, "remediation_actions", "action_id", seed.org_id, commit_action_id)

        # --- abort path: force the second write to fail (an invalid
        # `outcome` value the CHECK constraint rejects) and confirm the
        # first write in the SAME transaction does not survive either. ----
        abort_raised = False
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO episodic_events (
                            event_id, org_id, agent_id, incident_id, service_id,
                            event_type, content
                        )
                        VALUES (%s, %s, %s, %s, %s, 'action', 'atomicity probe: abort path')
                        """,
                        (abort_event_id, seed.org_id, seed.agent_id,
                         seed.incident_id, seed.service_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO remediation_actions (
                            action_id, org_id, incident_id, action_type, target_id,
                            applied_by, outcome, memory_ref, idempotency_key
                        )
                        VALUES (%s, %s, %s, 'restart_service', %s, 'resilience-harness',
                                'not_a_valid_outcome', %s, %s)
                        """,
                        (abort_action_id, seed.org_id, seed.incident_id,
                         seed.service_id, abort_event_id, f"resilience-atomicity-abort-{abort_action_id}"),
                    )
        except Exception:  # noqa: BLE001 - the CHECK-constraint violation, deliberately triggered
            abort_raised = True

        abort_episodic_present = _row_exists(conn, "episodic_events", "event_id", seed.org_id, abort_event_id)
        abort_action_present = _row_exists(conn, "remediation_actions", "action_id", seed.org_id, abort_action_id)
    finally:
        conn.close()

    commit_ok = commit_episodic_present and commit_action_present
    abort_ok = abort_raised and not abort_episodic_present and not abort_action_present
    status = "pass" if commit_ok and abort_ok else "fail"

    return ProbeResult(
        probe_type="atomicity",
        status=status,
        measured_value=1.0 if status == "pass" else 0.0,
        unit="bool",
        details={
            "commit_path": {
                "event_id": commit_event_id,
                "action_id": commit_action_id,
                "episodic_present": commit_episodic_present,
                "action_present": commit_action_present,
                "pass": commit_ok,
            },
            "abort_path": {
                "event_id": abort_event_id,
                "action_id": abort_action_id,
                "constraint_violation_raised": abort_raised,
                "episodic_present": abort_episodic_present,
                "action_present": abort_action_present,
                "pass": abort_ok,
            },
        },
    )
