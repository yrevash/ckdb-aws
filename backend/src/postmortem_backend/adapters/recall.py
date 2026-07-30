"""Direct, strongly-consistent CockroachDB memory recall adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from datetime import UTC, datetime
from time import sleep
from typing import Any
from uuid import UUID, uuid4

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
        source, valid_from, valid_to, recorded_at, superseded_by,
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
    ), ARRAY[]::UUID[]) AS provenance_ids,
    (
        SELECT jsonb_build_object(
            'fact_id', predecessor.fact_id,
            'object', predecessor.object,
            'confidence', predecessor.confidence,
            'valid_from', predecessor.valid_from,
            'valid_to', predecessor.valid_to,
            'recorded_at', predecessor.recorded_at,
            'source', predecessor.source
        )
        FROM semantic_facts AS predecessor
        WHERE predecessor.org_id = nearest.org_id
          AND predecessor.superseded_by = nearest.fact_id
        LIMIT 1
    ) AS predecessor
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


# Bitemporal transition: close the currently-valid fact (if any) and open the
# new one -- a fact is never overwritten in place. Mirrors
# research/postmortem/01-memory-architecture.md section 2, with one
# deliberate correction proven against a live cluster: that doc's sketch
# closes the old row and inserts the new row as a single multi-CTE
# statement, but CockroachDB rejects mixing an UPDATE and an INSERT against
# the *same* table in one statement by default
# (sql.multiple_modifications_of_table.enabled=false, "to prevent data
# corruption") -- and turning that guard off cluster-wide is not a trade this
# track makes for one query. So the transition is two statements in one
# explicit, client-retried transaction instead of one implicit
# server-retried statement: still fully atomic (both commit or neither
# does), just not single-round-trip. The insert runs first specifically so
# the FK on superseded_by (checked per-statement, not deferred) has a row to
# point at when the close/supersede update runs second.
INSERT_TRANSITIONED_FACT_SQL = """
INSERT INTO semantic_facts (
    fact_id, org_id, agent_id, subject, predicate, object, confidence,
    source, embedding, valid_from
)
VALUES (
    %(new_fact_id)s, %(org_id)s, %(agent_id)s, %(subject)s, %(predicate)s,
    %(object)s::JSONB, %(confidence)s, %(source)s,
    %(embedding)s::VECTOR(1024), %(transition_at)s
)
RETURNING fact_id, valid_from, recorded_at
"""

CLOSE_SUPERSEDED_FACT_SQL = """
UPDATE semantic_facts
SET valid_to = %(transition_at)s, superseded_by = %(new_fact_id)s
WHERE org_id = %(org_id)s
  AND subject = %(subject)s
  AND predicate = %(predicate)s
  AND valid_to IS NULL
  AND fact_id != %(new_fact_id)s
RETURNING fact_id
"""


# Full belief history for a single (subject, predicate), oldest first --
# powers the "why did the agent think X" audit/explainability view. Served by
# the semantic_facts_history covering index added in 0006_bitemporal_transitions.sql.
FACT_HISTORY_SQL = """
SELECT
    fact_id, object, confidence, source,
    valid_from, valid_to, recorded_at, superseded_by
FROM semantic_facts
WHERE org_id = %(org_id)s
  AND subject = %(subject)s
  AND predicate = %(predicate)s
ORDER BY recorded_at ASC
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


@dataclass(frozen=True, slots=True)
class FactTransitionResult:
    """Outcome of the atomic bitemporal transition -- close old, open new."""

    new_fact_id: UUID
    valid_from: datetime
    recorded_at: datetime
    superseded_fact_id: UUID | None


@dataclass(frozen=True, slots=True)
class FactHistoryEntry:
    """One row of a subject/predicate's full belief history, oldest first."""

    fact_id: UUID
    object: Any
    confidence: float
    source: str | None
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    superseded_by: UUID | None


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

    def transition_fact(
        self,
        *,
        org_id: UUID,
        agent_id: UUID,
        subject: str,
        predicate: str,
        object_value: Any,
        embedding: tuple[float, ...],
        confidence: float = 1.0,
        source: str | None = None,
        transition_at: datetime | None = None,
        max_serialization_retries: int = 3,
    ) -> FactTransitionResult:
        """Close the currently-valid fact (if any) and open the replacement,
        atomically -- never an in-place overwrite. Two statements
        (INSERT_TRANSITIONED_FACT_SQL, CLOSE_SUPERSEDED_FACT_SQL) inside one
        explicit transaction: both commit or neither does. Retries the whole
        transaction on 40001, never a single statement within it, per
        CockroachDB's serializable-retry contract.
        """

        if len(embedding) != 1024:
            raise RecallError("Fact embedding must have exactly 1024 dimensions.")
        params = {
            "new_fact_id": uuid4(),
            "org_id": org_id,
            "agent_id": agent_id,
            "subject": subject,
            "predicate": predicate,
            "object": json.dumps(object_value),
            "confidence": confidence,
            "source": source,
            "embedding": _vector_literal(embedding),
            # UTC-aware, never naive datetime.now(): a naive local timestamp
            # sent as a TIMESTAMPTZ literal is taken at face value, which
            # would skew valid_from away from the server's UTC recorded_at
            # by the host's offset -- exactly the kind of bug this schema's
            # bitemporal gate (valid_from <= as_of <= ...) is unforgiving of.
            "transition_at": transition_at or datetime.now(UTC),
        }

        for attempt in range(max_serialization_retries + 1):
            try:
                return self._transition_fact_once(params)
            except Exception as exc:
                if _sqlstate(exc) != "40001" or attempt >= max_serialization_retries:
                    raise RecallError("Bitemporal fact transition failed.") from exc
                sleep(0.025 * (2**attempt))
        raise AssertionError("serialization retry loop must return or raise")

    def _transition_fact_once(self, params: dict[str, Any]) -> FactTransitionResult:
        with self._connection_provider() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                    cursor.execute(INSERT_TRANSITIONED_FACT_SQL, params)
                    new_row = cursor.fetchone()
                    if new_row is None:
                        raise RecallError(
                            "Bitemporal fact transition insert returned no row."
                        )
                    _, valid_from, recorded_at = new_row
                    cursor.execute(CLOSE_SUPERSEDED_FACT_SQL, params)
                    superseded_row = cursor.fetchone()
        return FactTransitionResult(
            new_fact_id=_uuid(params["new_fact_id"]),
            valid_from=valid_from,
            recorded_at=recorded_at,
            superseded_fact_id=(
                _optional_uuid(superseded_row[0]) if superseded_row else None
            ),
        )

    def fact_history(
        self, *, org_id: UUID, subject: str, predicate: str
    ) -> tuple[FactHistoryEntry, ...]:
        """Full belief history for (subject, predicate), oldest first -- the
        audit/explainability view behind "why did the agent think X".
        """

        params = {"org_id": org_id, "subject": subject, "predicate": predicate}
        try:
            with self._connection_provider() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        rows = _fetch(cursor, FACT_HISTORY_SQL, params)
        except Exception as exc:
            raise RecallError("Fact history lookup failed.") from exc
        return tuple(
            FactHistoryEntry(
                fact_id=_uuid(row["fact_id"]),
                object=_json_value(row.get("object")),
                confidence=float(row.get("confidence") or 0.0),
                source=row.get("source"),
                valid_from=_datetime(row.get("valid_from")),
                valid_to=_datetime(row.get("valid_to")),
                recorded_at=_datetime(row.get("recorded_at")),
                superseded_by=_optional_uuid(row.get("superseded_by")),
            )
            for row in rows
        )

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
    predecessor = _json_value(row.get("predecessor"))
    metadata: dict[str, Any] = {
        "subject": row.get("subject"),
        "predicate": row.get("predicate"),
        "object": object_value,
        "source": row.get("source"),
        "valid_from": _text(row.get("valid_from")),
        "valid_to": _text(row.get("valid_to")),
        "learned_at": _text(row.get("recorded_at")),
        # Bitemporal audit trail: which fact (if any) this one superseded, and
        # which fact (if any) has since superseded this one. A non-null
        # `superseded_by` here would mean a stale fact leaked past the
        # valid_to filter in SEMANTIC_RECALL_SQL -- it should always be NULL
        # for a candidate returned as "currently valid at as_of".
        "superseded_by": _text(row.get("superseded_by")),
        "superseded_predecessor": predecessor if isinstance(predecessor, dict) else None,
    }
    return MemoryCandidate(
        memory_id=_uuid(row["fact_id"]),
        kind=MemoryKind.SEMANTIC,
        content=f"{row.get('subject')} {row.get('predicate')}: {object_value}",
        similarity=float(row.get("similarity") or 0.0),
        metadata=metadata,
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


def _sqlstate(exc: Exception) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(
        getattr(exc, "__cause__", None), "sqlstate", None
    )
