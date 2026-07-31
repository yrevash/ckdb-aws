"""Atomic CockroachDB adapter for verified remediation outcomes."""

from __future__ import annotations

from time import sleep
from typing import Any
from uuid import UUID

from ..domain import OutcomeCommand, OutcomeKind, OutcomeResult
from ..errors import OutcomeConflict, OutcomeRecordingError, ProvenanceError
from ..guardrails.roles import require_writer
from .cockroach import ConnectionProvider


RECORD_OUTCOME_SQL = """
WITH target AS MATERIALIZED (
    SELECT
        action.action_id,
        action.transaction_id,
        action.action_type,
        action.params,
        action.outcome AS prior_outcome,
        incident.runbook_id,
        incident.opened_at,
        incident.status AS incident_status
    FROM remediation_actions AS action
    JOIN incidents AS incident
      ON incident.org_id = action.org_id
     AND incident.incident_id = action.incident_id
    JOIN services AS service
      ON service.org_id = incident.org_id
     AND service.service_id = incident.service_id
    WHERE action.org_id = %(org_id)s
      AND action.action_id = %(action_id)s
      AND action.incident_id = %(incident_id)s
      AND action.target_id = %(service_id)s
      AND incident.service_id = %(service_id)s
    FOR UPDATE
),
existing AS MATERIALIZED (
    SELECT
        episode.event_id,
        episode.created_at,
        episode.metadata->>'outcome' AS recorded_outcome
    FROM episodic_events AS episode
    CROSS JOIN target
    WHERE episode.org_id = %(org_id)s
      AND episode.incident_id = %(incident_id)s
      AND episode.service_id = %(service_id)s
      AND episode.event_type = 'outcome'
      AND episode.metadata->>'action_id' = %(action_id_text)s
    ORDER BY episode.created_at
    LIMIT 1
),
action_update AS (
    UPDATE remediation_actions
    SET outcome = %(outcome)s
    FROM target
    WHERE remediation_actions.action_id = target.action_id
      AND NOT EXISTS (SELECT 1 FROM existing)
    RETURNING remediation_actions.action_id
),
incident_update AS (
    UPDATE incidents
    SET status = CASE
            WHEN %(outcome)s = 'success' THEN 'resolved'
            ELSE incidents.status
        END,
        resolved_at = CASE
            WHEN %(outcome)s = 'success'
                THEN COALESCE(incidents.resolved_at, %(observed_at)s)
            ELSE incidents.resolved_at
        END,
        mttr_seconds = CASE
            WHEN %(outcome)s = 'success'
                THEN COALESCE(
                    incidents.mttr_seconds,
                    GREATEST(
                        0,
                        CAST(EXTRACT(EPOCH FROM (%(observed_at)s - incidents.opened_at)) AS INT8)
                    )
                )
            ELSE incidents.mttr_seconds
        END
    FROM action_update
    WHERE incidents.org_id = %(org_id)s
      AND incidents.incident_id = %(incident_id)s
    RETURNING incidents.status
),
episode AS (
    INSERT INTO episodic_events (
        org_id, agent_id, incident_id, service_id, occurred_at,
        event_type, content, metadata, runbook_id, importance, embedding
    )
    SELECT
        %(org_id)s,
        %(agent_id)s,
        %(incident_id)s,
        %(service_id)s,
        %(observed_at)s,
        'outcome',
        %(content)s,
        jsonb_build_object(
            'outcome', %(outcome)s::STRING,
            'action_id', target.action_id,
            'transaction_id', target.transaction_id,
            'action_type', target.action_type,
            'params', target.params,
            'service_id', %(service_id)s::UUID,
            'error_signature', %(error_signature)s::STRING,
            'consolidation_ready', true,
            'source', 'responder_outcome'
        ),
        target.runbook_id,
        0.95,
        %(embedding)s::VECTOR(1024)
    FROM target
    CROSS JOIN action_update
    CROSS JOIN incident_update
    RETURNING event_id, created_at
)
SELECT
    existing.event_id,
    existing.created_at,
    target.transaction_id,
    target.incident_status,
    existing.recorded_outcome,
    true AS idempotent_replay
FROM existing
CROSS JOIN target
UNION ALL
SELECT
    episode.event_id,
    episode.created_at,
    target.transaction_id,
    incident_update.status,
    %(outcome)s::STRING AS recorded_outcome,
    false AS idempotent_replay
FROM episode
CROSS JOIN target
CROSS JOIN incident_update
LIMIT 1
"""


class CockroachOutcomeStore:
    """Records the verified outcome and closes the incident in one transaction."""

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        max_serialization_retries: int = 3,
    ) -> None:
        # The outcome write path is a writer-only path (R7/T2).
        require_writer(connection_provider)
        self._connection_provider = connection_provider
        self._max_serialization_retries = max_serialization_retries

    def record_outcome(
        self, command: OutcomeCommand, embedding: tuple[float, ...]
    ) -> OutcomeResult:
        if len(embedding) != 1024:
            raise OutcomeRecordingError(
                "Outcome embedding must have exactly 1024 dimensions."
            )
        params = {
            "org_id": command.org_id,
            "agent_id": command.agent_id,
            "incident_id": command.incident_id,
            "service_id": command.service_id,
            "action_id": command.action_id,
            "action_id_text": str(command.action_id),
            "outcome": command.outcome.value,
            "content": command.memory_text(),
            "error_signature": command.error_signature,
            "observed_at": command.observed_at,
            "embedding": _vector_literal(embedding),
        }
        for attempt in range(self._max_serialization_retries + 1):
            try:
                return self._execute_once(command, params)
            except Exception as exc:
                if _sqlstate(exc) != "40001" or attempt >= self._max_serialization_retries:
                    raise
                sleep(0.025 * (2**attempt))
        raise AssertionError("serialization retry loop must return or raise")

    def _execute_once(
        self, command: OutcomeCommand, params: dict[str, Any]
    ) -> OutcomeResult:
        try:
            with self._connection_provider() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        cursor.execute(RECORD_OUTCOME_SQL, params)
                        row = cursor.fetchone()
                        if row is None:
                            raise ProvenanceError(
                                "Outcome rejected: action does not belong to the "
                                "org, incident, and service."
                            )
                        (
                            event_id,
                            recorded_at,
                            transaction_id,
                            incident_status,
                            recorded_outcome,
                            idempotent_replay,
                        ) = row
                        if str(recorded_outcome) != command.outcome.value:
                            raise OutcomeConflict(
                                "An outcome is already recorded for this action "
                                f"as {recorded_outcome}."
                            )
        except (ProvenanceError, OutcomeConflict):
            raise
        except Exception as exc:
            if _sqlstate(exc) == "40001":
                raise
            raise OutcomeRecordingError(
                "CockroachDB rolled back outcome recording."
            ) from exc

        return OutcomeResult(
            action_id=command.action_id,
            transaction_id=_uuid(transaction_id),
            event_id=_uuid(event_id),
            incident_id=command.incident_id,
            outcome=OutcomeKind(str(recorded_outcome)),
            incident_status=str(incident_status),
            recorded_at=recorded_at,
            idempotent_replay=bool(idempotent_replay),
        )


def _vector_literal(embedding: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"


def _sqlstate(exc: Exception) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(
        getattr(exc, "__cause__", None), "sqlstate", None
    )


def _uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
