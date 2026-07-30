"""Minimal per-run fixture rows so the harness can write into
episodic_events / remediation_actions without violating foreign keys.

Follows the same convention as the rest of the repo's live tests
(backend/tests/test_cockroach_live.py, consolidation/tests/
test_repository_live.py): a fresh uuid4() org/service/incident/runbook per
run, self-contained, no shared fixture state, no teardown required (this
cluster is a throwaway demo cluster torn down by the calling script).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class SeedContext:
    org_id: str
    agent_id: str
    service_id: str
    incident_id: str
    runbook_id: str


def seed_baseline(conn) -> SeedContext:
    """Insert the minimal FK-satisfying row chain on `conn` (expects an open
    psycopg connection; commits internally). Idempotent per call -- always
    creates fresh rows scoped by a new org_id, so repeated harness runs never
    collide."""
    org_id = str(uuid4())
    agent_id = str(uuid4())
    service_id = str(uuid4())
    incident_id = str(uuid4())
    runbook_id = str(uuid4())

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (org_id, slug, display_name) "
            "VALUES (%s, %s, %s)",
            (org_id, f"resilience-harness-{org_id[:8]}", "Resilience harness (Phase 3, Track A)"),
        )
        cur.execute(
            "INSERT INTO services (service_id, org_id, name, current_version) "
            "VALUES (%s, %s, %s, %s)",
            (service_id, org_id, "checkout-resilience-probe", "v1"),
        )
        cur.execute(
            """
            INSERT INTO procedural_memory (
                runbook_id, org_id, agent_id, name, status,
                trigger_desc, steps
            )
            VALUES (%s, %s, %s, %s, 'active', %s, %s)
            """,
            (
                runbook_id, org_id, agent_id, "resilience-harness-runbook",
                "synthetic runbook used only by the resilience measurement harness",
                '[{"step": "no_op"}]',
            ),
        )
        cur.execute(
            """
            INSERT INTO incidents (
                incident_id, org_id, service_id, title, severity, status
            )
            VALUES (%s, %s, %s, %s, 'SEV3', 'mitigating')
            """,
            (incident_id, org_id, service_id, "Resilience harness synthetic incident"),
        )
    if not conn.autocommit:
        conn.commit()

    return SeedContext(
        org_id=org_id, agent_id=agent_id, service_id=service_id,
        incident_id=incident_id, runbook_id=runbook_id,
    )
