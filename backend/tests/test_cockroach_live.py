"""Opt-in proof that the production adapter executes against real CockroachDB."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from postmortem_backend.adapters.cockroach import (
    CockroachAtomicRemediationStore,
    PsycopgPoolProvider,
)
from postmortem_backend.adapters.recall import CockroachRecallAdapter
from postmortem_backend.adapters.outcome import CockroachOutcomeStore
from postmortem_backend.domain import (
    ActionKind,
    OutcomeCommand,
    OutcomeKind,
    RecallQuery,
    RemediationCommand,
)
from postmortem_backend.errors import ProvenanceError


DATABASE_URL = os.getenv("POSTMORTEM_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set POSTMORTEM_TEST_DATABASE_URL to run the live CockroachDB proof",
)
def test_remediation_and_memory_commit_together_in_live_cockroach() -> None:
    assert DATABASE_URL is not None
    ids = [uuid4() for _ in range(6)]
    org_id, agent_id, service_id, incident_id, session_id, runbook_id = ids
    embedding = tuple([1.0] + [0.0] * 1023)
    vector = "[" + ",".join(map(str, embedding)) + "]"

    with psycopg.connect(DATABASE_URL, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO organizations (org_id, slug, display_name) "
                    "VALUES (%s, %s, %s)",
                    (org_id, f"phase1-{org_id}", "Phase 1 live verification"),
                )
                cursor.execute(
                    """
                    INSERT INTO services (
                        service_id, org_id, name, tier, health,
                        current_version, previous_stable_version
                    )
                    VALUES (%s, %s, %s, 'critical-path', 'degraded', '1.5.0', '1.4.2')
                    """,
                    (service_id, org_id, f"checkout-{service_id}"),
                )
                cursor.execute(
                    """
                    INSERT INTO procedural_memory (
                        runbook_id, org_id, agent_id, name, status, trigger_desc,
                        embedding, steps, usage_count, success_count, success_rate,
                        created_by
                    )
                    VALUES (
                        %s, %s, %s, %s, 'active', %s, %s::VECTOR(1024),
                        %s::JSONB, 1, 1, 1.0, 'phase1-smoke'
                    )
                    """,
                    (
                        runbook_id,
                        org_id,
                        agent_id,
                        f"rollback-{runbook_id}",
                        "5xx spike after deploy",
                        vector,
                        '[{"action":"rollback"}]',
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, org_id, service_id, title, severity, status,
                        family_id, variant_id, runbook_id, session_id
                    )
                    VALUES (
                        %s, %s, %s, %s, 'SEV1', 'open',
                        'F1_BAD_DEPLOY', 'phase1-live', %s, %s
                    )
                    """,
                    (
                        incident_id,
                        org_id,
                        service_id,
                        "Phase 1 live transaction verification",
                        runbook_id,
                        session_id,
                    ),
                )

    pool = PsycopgPoolProvider(DATABASE_URL)
    try:
        result = CockroachAtomicRemediationStore(pool).remediate_and_record(
            RemediationCommand(
                org_id=org_id,
                agent_id=agent_id,
                incident_id=incident_id,
                session_id=session_id,
                service_id=service_id,
                action=ActionKind.ROLLBACK,
                target_version="1.4.2",
                cited_memory_id=runbook_id,
                runbook_id=runbook_id,
                rationale="Prior successful rollback matched this incident.",
            ),
            embedding,
        )
    finally:
        pool.close()

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.current_version, s.health, i.status,
                    ra.action_id, ra.transaction_id, ra.memory_ref,
                    ee.event_id, pm.usage_count
                FROM services AS s
                JOIN incidents AS i ON i.service_id = s.service_id
                JOIN remediation_actions AS ra ON ra.incident_id = i.incident_id
                JOIN episodic_events AS ee ON ee.event_id = ra.memory_ref
                JOIN procedural_memory AS pm ON pm.runbook_id = i.runbook_id
                WHERE s.org_id = %s AND i.incident_id = %s
                """,
                (org_id, incident_id),
            )
            row = cursor.fetchone()

    assert row is not None
    assert row[:3] == ("1.4.2", "recovering", "mitigating")
    assert row[3] == result.action_id
    assert row[4] == result.transaction_id
    assert row[5] == result.event_id == row[6]
    assert row[7] == 2

    outcome_pool = PsycopgPoolProvider(DATABASE_URL)
    try:
        outcome = CockroachOutcomeStore(outcome_pool).record_outcome(
            OutcomeCommand(
                org_id=org_id,
                agent_id=agent_id,
                incident_id=incident_id,
                service_id=service_id,
                action_id=result.action_id,
                outcome=OutcomeKind.SUCCESS,
                summary="Checkout error rate returned to baseline.",
                error_signature="HTTP_5XX_POST_DEPLOY",
            ),
            embedding,
        )
        replay = CockroachOutcomeStore(outcome_pool).record_outcome(
            OutcomeCommand(
                org_id=org_id,
                agent_id=agent_id,
                incident_id=incident_id,
                service_id=service_id,
                action_id=result.action_id,
                outcome=OutcomeKind.SUCCESS,
                summary="Checkout error rate returned to baseline.",
                error_signature="HTTP_5XX_POST_DEPLOY",
            ),
            embedding,
        )
    finally:
        outcome_pool.close()

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    incident.status,
                    action.outcome,
                    episode.event_type,
                    episode.metadata->>'outcome',
                    episode.metadata->>'action_type',
                    episode.metadata->>'consolidation_ready'
                FROM incidents AS incident
                JOIN remediation_actions AS action
                  ON action.incident_id = incident.incident_id
                JOIN episodic_events AS episode
                  ON episode.org_id = action.org_id
                 AND episode.incident_id = action.incident_id
                 AND episode.event_type = 'outcome'
                 AND episode.metadata->>'action_id' = action.action_id::STRING
                WHERE incident.org_id = %s AND incident.incident_id = %s
                """,
                (org_id, incident_id),
            )
            outcome_row = cursor.fetchone()

    assert outcome_row == (
        "resolved",
        "success",
        "outcome",
        "success",
        "rollback_deploy",
        "true",
    )
    assert outcome.event_id == replay.event_id
    assert replay.idempotent_replay is True


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set POSTMORTEM_TEST_DATABASE_URL to run the live CockroachDB proof",
)
def test_inactive_runbook_is_rejected_and_nothing_commits() -> None:
    """DB#5: REMEDIATE_AND_RECORD_SQL must gate on the cited runbook's
    *status*, not merely its existence -- a runbook that exists but was
    never promoted out of 'draft' (or has since been 'deprecated') must not
    be able to drive a live remediation. Before this fix, citing such a
    runbook silently proceeded because the query only checked the
    provenance CTE (does the id resolve to a row at all), never
    procedural_memory.status.
    """
    assert DATABASE_URL is not None
    ids = [uuid4() for _ in range(6)]
    org_id, agent_id, service_id, incident_id, session_id, runbook_id = ids
    embedding = tuple([1.0] + [0.0] * 1023)
    vector = "[" + ",".join(map(str, embedding)) + "]"

    with psycopg.connect(DATABASE_URL, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO organizations (org_id, slug, display_name) "
                    "VALUES (%s, %s, %s)",
                    (org_id, f"db5-{org_id}", "DB#5 runbook-gate live verification"),
                )
                cursor.execute(
                    """
                    INSERT INTO services (
                        service_id, org_id, name, tier, health,
                        current_version, previous_stable_version
                    )
                    VALUES (%s, %s, %s, 'critical-path', 'degraded', '1.5.0', '1.4.2')
                    """,
                    (service_id, org_id, f"checkout-{service_id}"),
                )
                cursor.execute(
                    """
                    INSERT INTO procedural_memory (
                        runbook_id, org_id, agent_id, name, status, trigger_desc,
                        embedding, steps, usage_count, success_count, success_rate,
                        created_by
                    )
                    VALUES (
                        %s, %s, %s, %s, 'draft', %s, %s::VECTOR(1024),
                        %s::JSONB, 0, 0, 0.0, 'db5-smoke'
                    )
                    """,
                    (
                        runbook_id,
                        org_id,
                        agent_id,
                        f"draft-rollback-{runbook_id}",
                        "5xx spike after deploy",
                        vector,
                        '[{"action":"rollback"}]',
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, org_id, service_id, title, severity, status,
                        family_id, variant_id, runbook_id, session_id
                    )
                    VALUES (
                        %s, %s, %s, %s, 'SEV1', 'open',
                        'F1_BAD_DEPLOY', 'db5-live', NULL, %s
                    )
                    """,
                    (
                        incident_id,
                        org_id,
                        service_id,
                        "DB#5 runbook-gate live verification",
                        session_id,
                    ),
                )

    pool = PsycopgPoolProvider(DATABASE_URL)
    try:
        with pytest.raises(ProvenanceError):
            CockroachAtomicRemediationStore(pool).remediate_and_record(
                RemediationCommand(
                    org_id=org_id,
                    agent_id=agent_id,
                    incident_id=incident_id,
                    session_id=session_id,
                    service_id=service_id,
                    action=ActionKind.ROLLBACK,
                    target_version="1.4.2",
                    cited_memory_id=runbook_id,
                    runbook_id=runbook_id,
                    rationale="Cited a draft (non-active) runbook.",
                ),
                embedding,
            )
    finally:
        pool.close()

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM remediation_actions "
                "WHERE org_id = %s AND incident_id = %s",
                (org_id, incident_id),
            )
            actions_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM deploys WHERE org_id = %s AND service_id = %s",
                (org_id, service_id),
            )
            deploys_count = cursor.fetchone()[0]
            cursor.execute(
                "SELECT status FROM incidents WHERE org_id = %s AND incident_id = %s",
                (org_id, incident_id),
            )
            incident_status = cursor.fetchone()[0]

    assert actions_count == 0
    assert deploys_count == 0
    assert incident_status == "open"


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set POSTMORTEM_TEST_DATABASE_URL to run the live CockroachDB proof",
)
def test_three_stage_recall_runs_against_live_cockroach() -> None:
    assert DATABASE_URL is not None
    (
        org_id,
        agent_id,
        service_id,
        incident_id,
        session_id,
        runbook_id,
        event_id,
        fact_id,
    ) = [uuid4() for _ in range(8)]
    embedding = tuple([1.0] + [0.0] * 1023)
    vector = "[" + ",".join(map(str, embedding)) + "]"
    service_name = f"checkout-{service_id}"

    with psycopg.connect(DATABASE_URL, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO organizations (org_id, slug, display_name) "
                    "VALUES (%s, %s, %s)",
                    (org_id, f"phase2-{org_id}", "Phase 2 recall verification"),
                )
                cursor.execute(
                    """
                    INSERT INTO services (
                        service_id, org_id, name, tier, health,
                        current_version, previous_stable_version
                    )
                    VALUES (%s, %s, %s, 'critical-path', 'degraded', '1.5.0', '1.4.2')
                    """,
                    (service_id, org_id, service_name),
                )
                cursor.execute(
                    """
                    INSERT INTO procedural_memory (
                        runbook_id, org_id, agent_id, name, status, trigger_desc,
                        embedding, applicable_service_tags,
                        applicable_error_signatures, steps, usage_count,
                        success_count, success_rate, last_used_at, created_by
                    )
                    VALUES (
                        %s, %s, %s, %s, 'active', %s, %s::VECTOR(1024),
                        ARRAY['checkout'], ARRAY['HTTP_5XX_POST_DEPLOY'],
                        %s::JSONB, 3, 3, 1.0, now(), 'phase2-smoke'
                    )
                    """,
                    (
                        runbook_id,
                        org_id,
                        agent_id,
                        f"rollback-{runbook_id}",
                        "checkout 5xx immediately after canary deploy",
                        vector,
                        '[{"step":1,"tool":"rollback_deploy"}]',
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, org_id, service_id, title, severity, status,
                        family_id, variant_id, runbook_id, session_id
                    )
                    VALUES (
                        %s, %s, %s, 'Prior canary failure', 'SEV1', 'closed',
                        'F1_BAD_DEPLOY', 'phase2-live', %s, %s
                    )
                    """,
                    (incident_id, org_id, service_id, runbook_id, session_id),
                )
                cursor.execute(
                    """
                    INSERT INTO episodic_events (
                        event_id, org_id, agent_id, incident_id, session_id,
                        service_id, event_type, content, metadata, runbook_id,
                        importance, embedding
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, 'outcome',
                        'Rollback restored checkout', '{"outcome":"success"}',
                        %s, 0.9, %s::VECTOR(1024)
                    )
                    """,
                    (
                        event_id,
                        org_id,
                        agent_id,
                        incident_id,
                        session_id,
                        service_id,
                        runbook_id,
                        vector,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO remediation_actions (
                        org_id, incident_id, action_type, target_id, params,
                        applied_by, outcome, memory_ref, idempotency_key
                    )
                    VALUES (
                        %s, %s, 'rollback_deploy', %s, '{"target_version":"1.4.2"}',
                        'agent:postmortem', 'success', %s, %s
                    )
                    """,
                    (org_id, incident_id, service_id, event_id, f"phase2-{event_id}"),
                )
                cursor.execute(
                    """
                    INSERT INTO runbook_provenance (
                        runbook_id, incident_id, episodic_event_id, role
                    )
                    VALUES (%s, %s, %s, 'source')
                    """,
                    (runbook_id, incident_id, event_id),
                )
                cursor.execute(
                    """
                    INSERT INTO semantic_facts (
                        fact_id, org_id, agent_id, subject, predicate, object,
                        confidence, source, embedding
                    )
                    VALUES (
                        %s, %s, %s, %s, 'safe_rollback_target',
                        '{"version":"1.4.2"}', 0.95, 'phase2-smoke',
                        %s::VECTOR(1024)
                    )
                    """,
                    (
                        fact_id,
                        org_id,
                        agent_id,
                        f"service:{service_name}",
                        vector,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO semantic_fact_provenance (
                        org_id, fact_id, incident_id, episodic_event_id, role
                    )
                    VALUES (%s, %s, %s, %s, 'source')
                    """,
                    (org_id, fact_id, incident_id, event_id),
                )

    pool = PsycopgPoolProvider(DATABASE_URL)
    try:
        result = CockroachRecallAdapter(pool).recall(
            RecallQuery(
                org_id=org_id,
                agent_id=agent_id,
                service_id=service_id,
                current_incident_id=uuid4(),
                text="checkout 5xx immediately after canary deploy",
                embedding=embedding,
                service_tags=("checkout", "critical-path"),
                error_signature="HTTP_5XX_POST_DEPLOY",
                as_of=datetime.now(UTC),
            )
        )
    finally:
        pool.close()

    assert [item.memory_id for item in result.episodes] == [event_id]
    assert [item.memory_id for item in result.facts] == [fact_id]
    assert [item.memory_id for item in result.runbooks] == [runbook_id]
    assert result.episodes[0].metadata["actionable"] is True
    assert result.facts[0].confidence == 0.95
    assert result.runbooks[0].metadata["provenance_verified"] is True
