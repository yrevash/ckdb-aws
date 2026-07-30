"""Direct, strongly-consistent CockroachDB memory recall adapter."""

from __future__ import annotations

from dataclasses import replace
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from ..domain import MemoryCandidate, MemoryKind, RecallBundle, RecallQuery
from ..errors import RecallError
from ..recall import RecallPolicy, RecallRanker
from .cockroach import ConnectionProvider


EPISODIC_RECALL_SQL = """
WITH nearest AS (
    SELECT
        event_id, org_id, agent_id, incident_id, service_id, occurred_at,
        content, metadata, runbook_id, created_at,
        1 - (embedding <=> %(embedding)s::VECTOR(1024)) AS similarity
    FROM episodic_events
    WHERE org_id = %(org_id)s
      AND agent_id = %(agent_id)s
      AND embedding IS NOT NULL
      AND occurred_at <= %(as_of)s
      AND (incident_id IS NULL OR incident_id != %(current_incident_id)s)
    ORDER BY embedding <=> %(embedding)s::VECTOR(1024)
    LIMIT %(candidate_limit)s
)
SELECT
    nearest.*,
    nearest.incident_id AS source_case_id,
    action.action_id,
    action.action_type AS successful_action,
    action.outcome,
    action.transaction_id
FROM nearest
LEFT JOIN LATERAL (
    SELECT action_id, action_type, outcome, transaction_id
    FROM remediation_actions
    WHERE org_id = %(org_id)s AND memory_ref = nearest.event_id
    ORDER BY applied_at DESC
    LIMIT 1
) AS action ON true
WHERE nearest.service_id = %(service_id)s OR nearest.service_id IS NULL
"""


SEMANTIC_RECALL_SQL = """
WITH nearest AS (
    SELECT
        fact_id, org_id, agent_id, subject, predicate, object, confidence,
        source, valid_from, valid_to, recorded_at,
        1 - (embedding <=> %(embedding)s::VECTOR(1024)) AS similarity
    FROM semantic_facts
    WHERE org_id = %(org_id)s
      AND agent_id = %(agent_id)s
      AND embedding IS NOT NULL
      AND valid_from <= %(as_of)s
      AND (valid_to IS NULL OR valid_to > %(as_of)s)
      AND recorded_at <= %(as_of)s
    ORDER BY embedding <=> %(embedding)s::VECTOR(1024)
    LIMIT %(candidate_limit)s
)
SELECT
    nearest.*,
    CASE WHEN nearest.subject LIKE 'service:%%' THEN %(service_id)s ELSE NULL END
        AS scoped_service_id,
    COALESCE((
        SELECT array_agg(episodic_event_id)
        FROM semantic_fact_provenance
        WHERE org_id = nearest.org_id
          AND fact_id = nearest.fact_id
          AND episodic_event_id IS NOT NULL
          AND role IN ('source', 'reinforcement')
    ), ARRAY[]::UUID[]) AS provenance_ids
FROM nearest
WHERE nearest.subject LIKE 'org:%%'
   OR nearest.subject IN (
       SELECT 'service:' || name
       FROM services
       WHERE org_id = %(org_id)s AND service_id = %(service_id)s
       UNION ALL
       SELECT 'service:' || service_id::STRING
       FROM services
       WHERE org_id = %(org_id)s AND service_id = %(service_id)s
   )
"""


PROCEDURAL_RECALL_SQL = """
WITH nearest AS (
    SELECT
        runbook_id, org_id, agent_id, name, version, trigger_desc,
        applicable_service_tags, applicable_error_signatures, preconditions,
        steps, postconditions, usage_count, success_count, failure_count,
        success_rate, avg_resolution_seconds, last_used_at, created_by, created_at,
        1 - (embedding <=> %(embedding)s::VECTOR(1024)) AS similarity
    FROM procedural_memory
    WHERE org_id = %(org_id)s
      AND status = 'active'
      AND agent_id = %(agent_id)s
      AND embedding IS NOT NULL
      AND created_at <= %(as_of)s
    ORDER BY embedding <=> %(embedding)s::VECTOR(1024)
    LIMIT %(candidate_limit)s
)
SELECT
    nearest.*,
    COALESCE((
        SELECT count(*)
        FROM runbook_provenance
        WHERE runbook_id = nearest.runbook_id
          AND role IN ('source', 'reinforcement')
    ), 0) AS positive_provenance_count,
    COALESCE((
        SELECT count(*)
        FROM runbook_provenance
        WHERE runbook_id = nearest.runbook_id
          AND role = 'counterexample'
    ), 0) AS counterexample_count,
    COALESCE((
        SELECT array_agg(episodic_event_id)
        FROM runbook_provenance
        WHERE runbook_id = nearest.runbook_id
          AND role IN ('source', 'reinforcement')
          AND episodic_event_id IS NOT NULL
    ), ARRAY[]::UUID[]) AS provenance_ids
FROM nearest
"""


class CockroachRecallAdapter:
    """Leaseholder-read recall with application-side safety gates and reranking."""

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        policy: RecallPolicy | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._ranker = RecallRanker(policy)

    def recall(self, query: RecallQuery) -> RecallBundle:
        if query.cold_start:
            return self._ranker.rank(query)
        if len(query.embedding) != 1024:
            raise RecallError("Recall embedding must have exactly 1024 dimensions.")

        params = {
            "org_id": query.org_id,
            "agent_id": query.agent_id,
            "service_id": query.service_id,
            "current_incident_id": query.current_incident_id or UUID(int=0),
            "as_of": query.as_of,
            "embedding": _vector_literal(query.embedding),
            "candidate_limit": self._ranker.policy.candidate_limit(query.k),
        }
        try:
            with self._connection_provider() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        episodes = _fetch(cursor, EPISODIC_RECALL_SQL, params)
                        facts = _fetch(cursor, SEMANTIC_RECALL_SQL, params)
                        runbooks = _fetch(cursor, PROCEDURAL_RECALL_SQL, params)
        except Exception as exc:
            raise RecallError("Direct CockroachDB memory recall failed.") from exc

        result = self._ranker.rank(
            query,
            episodes=tuple(_episode(row) for row in episodes),
            facts=tuple(_fact(row) for row in facts),
            runbooks=tuple(_runbook(row) for row in runbooks),
        )
        return replace(
            result,
            diagnostics={**result.diagnostics, "provider": "c-spann+sql"},
        )


def _fetch(cursor: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    columns = [
        description.name if hasattr(description, "name") else description[0]
        for description in cursor.description
    ]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _episode(row: dict[str, Any]) -> MemoryCandidate:
    metadata = _json_object(row.get("metadata"))
    metadata.update(
        {
            "incident_id": _text(row.get("incident_id")),
            "source_case_id": _text(row.get("source_case_id")),
            "action_id": _text(row.get("action_id")),
            "successful_action": row.get("successful_action"),
            "outcome": row.get("outcome"),
            "transaction_id": _text(row.get("transaction_id")),
            "learned_at": _text(row.get("created_at")),
        }
    )
    return MemoryCandidate(
        memory_id=_uuid(row["event_id"]),
        kind=MemoryKind.EPISODIC,
        content=str(row.get("content") or ""),
        similarity=float(row.get("similarity") or 0.0),
        occurred_at=_datetime(row.get("occurred_at")),
        service_id=_optional_uuid(row.get("service_id")),
        runbook_id=_optional_uuid(row.get("runbook_id")),
        metadata=metadata,
        org_id=_optional_uuid(row.get("org_id")),
        agent_id=_optional_uuid(row.get("agent_id")),
        recorded_at=_datetime(row.get("created_at")),
        provenance_ids=tuple(
            value
            for value in (
                _optional_uuid(row.get("action_id")),
                _optional_uuid(row.get("transaction_id")),
            )
            if value is not None
        ),
    )


def _fact(row: dict[str, Any]) -> MemoryCandidate:
    object_value = _json_value(row.get("object"))
    return MemoryCandidate(
        memory_id=_uuid(row["fact_id"]),
        kind=MemoryKind.SEMANTIC,
        content=f"{row.get('subject')} {row.get('predicate')}: {object_value}",
        similarity=float(row.get("similarity") or 0.0),
        metadata={
            "subject": row.get("subject"),
            "predicate": row.get("predicate"),
            "object": object_value,
            "source": row.get("source"),
            "valid_from": _text(row.get("valid_from")),
            "learned_at": _text(row.get("recorded_at")),
        },
        org_id=_optional_uuid(row.get("org_id")),
        agent_id=_optional_uuid(row.get("agent_id")),
        service_id=_optional_uuid(row.get("scoped_service_id")),
        confidence=float(row.get("confidence") or 0.0),
        valid_from=_datetime(row.get("valid_from")),
        valid_to=_datetime(row.get("valid_to")),
        recorded_at=_datetime(row.get("recorded_at")),
        provenance_ids=_uuid_tuple(row.get("provenance_ids")),
    )


def _runbook(row: dict[str, Any]) -> MemoryCandidate:
    steps = _json_value(row.get("steps")) or []
    metadata = {
        "name": row.get("name"),
        "version": row.get("version"),
        "preconditions": _json_value(row.get("preconditions")) or [],
        "postconditions": _json_value(row.get("postconditions")) or [],
        "applicable_service_tags": tuple(row.get("applicable_service_tags") or ()),
        "applicable_error_signatures": tuple(
            row.get("applicable_error_signatures") or ()
        ),
        "usage_count": int(row.get("usage_count") or 0),
        "success_count": int(row.get("success_count") or 0),
        "failure_count": int(row.get("failure_count") or 0),
        "positive_provenance_count": int(
            row.get("positive_provenance_count") or 0
        ),
        "counterexample_count": int(row.get("counterexample_count") or 0),
        "last_used_at": _text(row.get("last_used_at")),
        "created_by": row.get("created_by"),
        "learned_at": _text(row.get("created_at")),
    }
    return MemoryCandidate(
        memory_id=_uuid(row["runbook_id"]),
        kind=MemoryKind.PROCEDURAL,
        content=str(row.get("trigger_desc") or row.get("name") or ""),
        similarity=float(row.get("similarity") or 0.0),
        success_rate=float(row.get("success_rate") or 0.0),
        runbook_id=_uuid(row["runbook_id"]),
        steps=tuple(steps),
        metadata=metadata,
        org_id=_optional_uuid(row.get("org_id")),
        agent_id=_optional_uuid(row.get("agent_id")),
        recorded_at=_datetime(row.get("created_at")),
        provenance_ids=_uuid_tuple(row.get("provenance_ids")),
    )


def _vector_literal(embedding: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_uuid(value: Any) -> UUID | None:
    return _uuid(value) if value else None


def _uuid_tuple(value: Any) -> tuple[UUID, ...]:
    if not value:
        return ()
    return tuple(_uuid(item) for item in value if item)


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)) if value else None


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None
