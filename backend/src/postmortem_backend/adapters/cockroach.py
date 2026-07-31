"""Direct CockroachDB adapter for the one-transaction wedge."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from time import sleep
from typing import Any, Protocol
from uuid import UUID

from ..domain import ActionKind, RemediationCommand, RemediationResult
from ..errors import (
    ApprovalRequired,
    AtomicRemediationError,
    ProvenanceError,
    UnsupportedAction,
)
from ..guardrails.roles import require_writer


REMEDIATE_AND_RECORD_SQL = """
WITH provenance AS MATERIALIZED (
    SELECT event_id AS cited_id, 'episodic'::STRING AS kind
    FROM episodic_events
    WHERE org_id = %(org_id)s AND event_id = %(cited_memory_id)s
    UNION ALL
    SELECT runbook_id AS cited_id, 'procedural'::STRING AS kind
    FROM procedural_memory
    WHERE org_id = %(org_id)s AND runbook_id = %(cited_memory_id)s
    LIMIT 1
),
target AS MATERIALIZED (
    SELECT i.incident_id, i.session_id, i.service_id
    FROM incidents AS i
    JOIN services AS s
      ON s.service_id = i.service_id AND s.org_id = i.org_id
    WHERE i.org_id = %(org_id)s
      AND i.incident_id = %(incident_id)s
      AND i.service_id = %(service_id)s
      AND i.status IN ('open', 'mitigating')
    FOR UPDATE
),
action AS (
    INSERT INTO deploys (
        org_id, service_id, version, action, deployed_by, status
    )
    SELECT
        %(org_id)s, target.service_id, %(target_version)s, %(action)s,
        %(deployed_by)s, 'completed'
    FROM target
    CROSS JOIN provenance
    RETURNING deploy_id
),
service_update AS (
    UPDATE services
    SET health = 'recovering',
        current_version = %(target_version)s,
        current_deploy_id = action.deploy_id,
        updated_at = now()
    FROM action
    WHERE services.org_id = %(org_id)s
      AND services.service_id = %(service_id)s
    RETURNING services.service_id
),
incident_update AS (
    UPDATE incidents
    SET status = 'mitigating',
        runbook_id = %(runbook_id)s
    FROM action
    WHERE incidents.org_id = %(org_id)s
      AND incidents.incident_id = %(incident_id)s
    RETURNING incidents.incident_id
),
episode AS (
    INSERT INTO episodic_events (
        org_id, agent_id, incident_id, session_id, service_id, event_type,
        content, metadata, runbook_id, importance, embedding
    )
    SELECT
        %(org_id)s, %(agent_id)s, %(incident_id)s, %(session_id)s,
        %(service_id)s, 'action', %(content)s,
        jsonb_build_object(
            'deploy_id', action.deploy_id,
            'cited_memory_id', %(cited_memory_id)s::UUID,
            'outcome', %(outcome)s::STRING
        ),
        %(runbook_id)s, 0.9, %(embedding)s::VECTOR(1024)
    FROM action
    CROSS JOIN service_update
    CROSS JOIN incident_update
    RETURNING event_id, created_at
),
record_action AS (
    INSERT INTO remediation_actions (
        org_id, incident_id, action_type, target_id, params, applied_by,
        outcome, memory_ref, idempotency_key
    )
    SELECT
        %(org_id)s, %(incident_id)s, %(action_type)s, %(service_id)s,
        jsonb_build_object('target_version', %(target_version)s::STRING),
        %(deployed_by)s, 'success', episode.event_id, %(idempotency_key)s
    FROM episode
    RETURNING action_id, transaction_id, applied_at
),
runbook_usage AS (
    UPDATE procedural_memory
    SET usage_count = usage_count + 1,
        last_used_at = now()
    WHERE org_id = %(org_id)s
      AND runbook_id = %(runbook_id)s
      AND EXISTS (SELECT 1 FROM episode)
    RETURNING runbook_id
)
SELECT
    action.deploy_id,
    episode.event_id,
    record_action.action_id,
    record_action.transaction_id,
    record_action.applied_at
FROM action
CROSS JOIN episode
CROSS JOIN record_action
"""


# Idempotent-replay lookup: resolve the already-committed action for a given
# (org_id, idempotency_key) so a duplicate remediation returns the original
# result rather than a unique-constraint error (audit backend#2 / DB#3).
REPLAY_LOOKUP_SQL = """
SELECT
    ra.action_id,
    ra.transaction_id,
    ra.applied_at,
    ra.memory_ref,
    (ee.metadata->>'deploy_id')::UUID AS deploy_id
FROM remediation_actions AS ra
LEFT JOIN episodic_events AS ee
  ON ee.event_id = ra.memory_ref AND ee.org_id = ra.org_id
WHERE ra.org_id = %(org_id)s
  AND ra.idempotency_key = %(idempotency_key)s
LIMIT 1
"""


class Cursor(Protocol):
    def execute(self, query: str, params: dict[str, Any] | None = None) -> None: ...

    def fetchone(self) -> tuple[Any, ...] | None: ...

    def __enter__(self) -> "Cursor": ...

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def transaction(self) -> AbstractContextManager[Any]: ...


ConnectionProvider = Callable[[], AbstractContextManager[Connection]]


class CockroachAtomicRemediationStore:
    """Executes the operational mutation and memory append in one SERIALIZABLE txn."""

    SUPPORTED_ACTIONS = frozenset({ActionKind.ROLLBACK, ActionKind.RESTART})

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        max_serialization_retries: int = 3,
    ) -> None:
        # Structural least-privilege (R7/T2): the act path can only be wired to a
        # write-capable identity. A reader-scoped pool is refused here, before any
        # statement runs. Unscoped providers (tests/local) pass through.
        require_writer(connection_provider)
        self._connection_provider = connection_provider
        self._max_serialization_retries = max_serialization_retries

    def remediate_and_record(
        self, command: RemediationCommand, embedding: tuple[float, ...]
    ) -> RemediationResult:
        self._validate(command, embedding)
        params = {
            "org_id": command.org_id,
            "agent_id": command.agent_id,
            "incident_id": command.incident_id,
            "session_id": command.session_id,
            "service_id": command.service_id,
            "action": command.action.value,
            "action_type": (
                "rollback_deploy"
                if command.action is ActionKind.ROLLBACK
                else "restart_service"
            ),
            "target_version": command.target_version,
            "deployed_by": f"agent:{command.agent_id}",
            "cited_memory_id": command.cited_memory_id,
            "runbook_id": command.runbook_id,
            "content": command.memory_text(),
            "outcome": command.outcome_stub,
            "idempotency_key": command.effective_idempotency_key(),
            "embedding": _vector_literal(embedding),
        }

        for attempt in range(self._max_serialization_retries + 1):
            try:
                return self._execute_once(params)
            except Exception as exc:
                state = _sqlstate(exc)
                # An idempotency-key collision (23505) means a prior attempt for
                # this exact command already committed (e.g. the client retried a
                # request whose response was lost). Replay the original result
                # instead of surfacing a misleading conflict/rollback (audit
                # backend#2 / DB#3).
                if state == "23505":
                    replay = self._replay(params)
                    if replay is not None:
                        return replay
                    raise
                if state != "40001" or attempt >= self._max_serialization_retries:
                    raise
                sleep(0.025 * (2**attempt))
        raise AssertionError("serialization retry loop must return or raise")

    def _replay(self, params: dict[str, Any]) -> RemediationResult | None:
        """Return the already-committed result for this idempotency key, if any."""
        try:
            with self._connection_provider() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(REPLAY_LOOKUP_SQL, params)
                        row = cursor.fetchone()
        except Exception:
            return None
        if row is None:
            return None
        action_id, transaction_id, applied_at, event_id, deploy_id = row
        return RemediationResult(
            action_id=_uuid(action_id),
            transaction_id=_uuid(transaction_id),
            deploy_id=_uuid(deploy_id) if deploy_id is not None else None,
            event_id=_uuid(event_id),
            committed_at=applied_at,
        )

    def _execute_once(self, params: dict[str, Any]) -> RemediationResult:
        try:
            with self._connection_provider() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        cursor.execute(REMEDIATE_AND_RECORD_SQL, params)
                        row = cursor.fetchone()
                        if row is None:
                            raise ProvenanceError(
                                "Atomic action rejected: incident, service, or cited memory "
                                "did not satisfy the provenance gate."
                            )
                        deploy_id, event_id, action_id, transaction_id, committed_at = row
        except (ProvenanceError, ApprovalRequired, UnsupportedAction):
            raise
        except Exception as exc:
            if _sqlstate(exc) == "40001":
                raise
            raise AtomicRemediationError(
                "CockroachDB rolled back remediate_and_record."
            ) from exc

        return RemediationResult(
            action_id=_uuid(action_id),
            transaction_id=_uuid(transaction_id),
            deploy_id=_uuid(deploy_id),
            event_id=_uuid(event_id),
            committed_at=committed_at,
        )

    def _validate(
        self, command: RemediationCommand, embedding: tuple[float, ...]
    ) -> None:
        if command.action not in self.SUPPORTED_ACTIONS:
            raise UnsupportedAction(
                f"{command.action.value} is outside the Phase 1 SQL allowlist."
            )
        if command.requires_human_approval and not command.approved:
            raise ApprovalRequired("This remediation requires explicit SRE approval.")
        if len(embedding) != 1024:
            raise AtomicRemediationError("Titan embedding must have exactly 1024 dimensions.")
        if not command.target_version.strip():
            raise AtomicRemediationError("target_version cannot be blank.")


class PsycopgPoolProvider:
    """Lazy psycopg pool wrapper so importing the core never requires psycopg."""

    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 4) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised only in deployed mode
            raise RuntimeError("Install psycopg[binary,pool] for AWS runtime mode.") from exc
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"autocommit": False},
        )

    def __call__(self) -> AbstractContextManager[Connection]:
        return self._pool.connection()

    def close(self) -> None:
        self._pool.close()


def _vector_literal(embedding: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"


def _sqlstate(exc: Exception) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(
        getattr(exc, "__cause__", None), "sqlstate", None
    )


def _uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
