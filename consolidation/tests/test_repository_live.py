from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from postmortem_consolidation.contracts import CandidateRunbook
from postmortem_consolidation.embedding import DeterministicEmbeddingModel
from postmortem_consolidation.repository import CockroachRunbookRepository

DATABASE_URL = os.getenv("POSTMORTEM_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="POSTMORTEM_TEST_DATABASE_URL is not set",
)


def test_live_create_promote_weaken_and_replay_contract() -> None:
    psycopg = pytest.importorskip("psycopg")
    org_id, service_id, agent_id = str(uuid4()), str(uuid4()), str(uuid4())
    incident_ids = [str(uuid4()) for _ in range(4)]
    event_ids = [str(uuid4()) for _ in range(4)]
    repository = CockroachRunbookRepository(
        DATABASE_URL, reinforcements_to_activate=2
    )
    embedding = DeterministicEmbeddingModel().embed("p99-after-canary")

    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO organizations (org_id, slug, display_name)
                    VALUES (%s, %s, 'Consolidation integration test')
                    """,
                    (org_id, f"consolidation-test-{org_id}"),
                )
                cursor.execute(
                    """
                    INSERT INTO services (
                        service_id, org_id, name, current_version,
                        previous_stable_version
                    )
                    VALUES (%s, %s, 'checkout-test', '#5120', '#5119')
                    """,
                    (service_id, org_id),
                )
                for index, (incident_id, event_id) in enumerate(
                    zip(incident_ids, event_ids, strict=True)
                ):
                    cursor.execute(
                        """
                        INSERT INTO incidents (
                            incident_id, org_id, service_id, title, severity,
                            status, resolved_at
                        )
                        VALUES (
                            %s, %s, %s, %s, 'SEV1', 'resolved', now()
                        )
                        """,
                        (incident_id, org_id, service_id, f"recurrence-{index}"),
                    )
                    cursor.execute(
                        """
                        INSERT INTO episodic_events (
                            event_id, org_id, agent_id, incident_id, service_id,
                            event_type, content
                        )
                        VALUES (%s, %s, %s, %s, %s, 'outcome', 'resolved')
                        """,
                        (event_id, org_id, agent_id, incident_id, service_id),
                    )

                base = CandidateRunbook(
                    org_id=org_id,
                    agent_id=agent_id,
                    incident_id=incident_ids[0],
                    service_id=service_id,
                    name="checkout-test-bad-deploy",
                    trigger_desc="p99-after-canary",
                    steps=(
                        {
                            "order": 1,
                            "tool": "remediate_and_record",
                            "action": "rollback_deploy",
                        },
                    ),
                    preconditions=(),
                    postconditions=(),
                    service_tags=("checkout-test",),
                    error_signatures=("p99-after-canary",),
                    outcome="success",
                    source_event_ids=(event_ids[0],),
                    embedding=embedding,
                )

                created = repository.apply(base, "window-1")
                reinforced = repository.apply(
                    replace(
                        base,
                        incident_id=incident_ids[1],
                        source_event_ids=(event_ids[1],),
                    ),
                    "window-2",
                )
                promoted = repository.apply(
                    replace(
                        base,
                        incident_id=incident_ids[2],
                        source_event_ids=(event_ids[2],),
                    ),
                    "window-3",
                )

                # Cross-component exit gate: the responder sees the promoted
                # runbook on the first read through its production recall adapter.
                from postmortem_backend.adapters.recall import (
                    CockroachRecallAdapter,
                )
                from postmortem_backend.domain import RecallQuery

                @contextmanager
                def provider():
                    with psycopg.connect(DATABASE_URL) as recall_connection:
                        yield recall_connection

                query = RecallQuery(
                    org_id=UUID(org_id),
                    agent_id=UUID(agent_id),
                    service_id=UUID(service_id),
                    current_incident_id=uuid4(),
                    text="p99 after checkout canary",
                    embedding=embedding,
                    service_tags=("checkout-test",),
                    error_signature="p99-after-canary",
                    as_of=datetime.now(UTC),
                )
                recalled = CockroachRecallAdapter(provider).recall(query)
                isolated = CockroachRecallAdapter(provider).recall(
                    replace(query, org_id=uuid4())
                )

                assert [str(item.memory_id) for item in recalled.runbooks] == [
                    created.runbook_id
                ]
                assert isolated.runbooks == ()

                replay = repository.apply(base, "window-1")
                weakened = repository.apply(
                    replace(
                        base,
                        incident_id=incident_ids[3],
                        source_event_ids=(event_ids[3],),
                        outcome="failed",
                    ),
                    "window-4",
                )

                assert (created.status, reinforced.status, promoted.status) == (
                    "draft",
                    "draft",
                    "active",
                )
                assert replay.idempotent_replay is True
                assert weakened.operation == "weaken"

                cursor.execute(
                    """
                    SELECT status, success_count, failure_count,
                           embedding IS NOT NULL
                    FROM procedural_memory
                    WHERE runbook_id = %s
                    """,
                    (created.runbook_id,),
                )
                assert cursor.fetchone() == ("active", 3, 1, True)
            finally:
                cursor.execute(
                    """
                    DELETE FROM runbook_provenance
                    WHERE runbook_id IN (
                        SELECT runbook_id FROM procedural_memory WHERE org_id = %s
                    )
                    """,
                    (org_id,),
                )
                cursor.execute(
                    "DELETE FROM procedural_memory WHERE org_id = %s", (org_id,)
                )
                cursor.execute(
                    "DELETE FROM episodic_events WHERE org_id = %s", (org_id,)
                )
                cursor.execute("DELETE FROM incidents WHERE org_id = %s", (org_id,))
                cursor.execute("DELETE FROM services WHERE org_id = %s", (org_id,))
                cursor.execute(
                    "DELETE FROM organizations WHERE org_id = %s", (org_id,)
                )
