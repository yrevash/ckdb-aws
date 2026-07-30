"""Read-only CockroachDB Managed MCP recall adapter."""

from __future__ import annotations

from dataclasses import replace
import json
from datetime import datetime
from typing import Any, Protocol
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from ..domain import MemoryCandidate, MemoryKind, RecallBundle, RecallQuery
from ..errors import RecallError
from ..recall import RecallPolicy, RecallRanker


class MCPTransport(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class StreamableHttpMCPTransport:
    """Minimal MCP Streamable HTTP client for the managed `select_query` tool."""

    def __init__(self, endpoint: str, token: str, *, timeout_seconds: float = 10.0) -> None:
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout_seconds
        self._session_id: str | None = None
        self._initialize()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self._rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def _initialize(self) -> None:
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "postmortem-backend", "version": "0.1.0"},
            },
        )
        self._rpc("notifications/initialized", None, notification=True)

    def _rpc(
        self, method: str, params: dict[str, Any] | None, *, notification: bool = False
    ) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            payload["id"] = str(uuid4())
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                self._session_id = response.headers.get("Mcp-Session-Id", self._session_id)
                body = response.read().decode("utf-8")
        except Exception as exc:
            raise RecallError("CockroachDB Managed MCP request failed.") from exc
        if notification or not body:
            return None
        if body.lstrip().startswith("data:"):
            body = "\n".join(
                line[5:].strip() for line in body.splitlines() if line.startswith("data:")
            )
        response_payload = json.loads(body)
        if "error" in response_payload:
            raise RecallError(f"Managed MCP error: {response_payload['error']}")
        return response_payload.get("result")


class ManagedMCPRecallAdapter:
    """Runs the three authoritative read-only recall queries through MCP."""

    def __init__(
        self, transport: MCPTransport, *, policy: RecallPolicy | None = None
    ) -> None:
        self._transport = transport
        self._ranker = RecallRanker(policy)

    def recall(self, query: RecallQuery) -> RecallBundle:
        if query.cold_start:
            return self._ranker.rank(query)
        vector = _vector_literal(query.embedding)
        policy = self._ranker.policy
        episodes = self._select(_episodic_sql(query, vector, policy))
        facts = self._select(_semantic_sql(query, vector, policy))
        runbooks = self._select(_procedural_sql(query, vector, policy))
        result = self._ranker.rank(
            query,
            episodes=tuple(_episode(row) for row in episodes),
            facts=tuple(_fact(row) for row in facts),
            runbooks=tuple(_runbook(row) for row in runbooks),
        )
        return replace(
            result,
            diagnostics={**result.diagnostics, "provider": "c-spann+mcp"},
        )

    def _select(self, sql: str) -> list[dict[str, Any]]:
        result = self._transport.call_tool("select_query", {"query": sql})
        payload = _unwrap(result)
        if isinstance(payload, dict):
            rows = payload.get("rows") or payload.get("data") or payload.get("result") or []
        else:
            rows = payload
        if not isinstance(rows, list):
            raise RecallError("Managed MCP select_query returned an unexpected row envelope.")
        return [row for row in rows if isinstance(row, dict)]


def _episodic_sql(
    query: RecallQuery, vector: str, policy: RecallPolicy
) -> str:
    current_incident = query.current_incident_id or UUID(int=0)
    limit = policy.candidate_limit(query.k)
    return f"""
WITH nearest AS (
  SELECT event_id, org_id, agent_id, incident_id, service_id, occurred_at,
         content, metadata, runbook_id, created_at,
         1 - (embedding <=> '{vector}'::VECTOR(1024)) AS similarity
  FROM episodic_events
  WHERE org_id = '{query.org_id}' AND agent_id = '{query.agent_id}'
    AND embedding IS NOT NULL
    AND occurred_at <= '{query.as_of.isoformat()}'::TIMESTAMPTZ
    AND (incident_id IS NULL OR incident_id != '{current_incident}')
  ORDER BY embedding <=> '{vector}'::VECTOR(1024)
  LIMIT {limit}
)
SELECT nearest.*, nearest.incident_id AS source_case_id,
       action.action_id, action.action_type AS successful_action,
       action.outcome, action.transaction_id
FROM nearest
LEFT JOIN LATERAL (
  SELECT action_id, action_type, outcome, transaction_id
  FROM remediation_actions
  WHERE org_id = '{query.org_id}' AND memory_ref = nearest.event_id
  ORDER BY applied_at DESC LIMIT 1
) AS action ON true
WHERE nearest.service_id = '{query.service_id}' OR nearest.service_id IS NULL
""".strip()


def _semantic_sql(
    query: RecallQuery, vector: str, policy: RecallPolicy
) -> str:
    limit = policy.candidate_limit(query.k)
    return f"""
WITH nearest AS (
  SELECT fact_id, org_id, agent_id, subject, predicate, object, confidence,
         source, valid_from, valid_to, recorded_at,
         1 - (embedding <=> '{vector}'::VECTOR(1024)) AS similarity
  FROM semantic_facts
  WHERE org_id = '{query.org_id}' AND agent_id = '{query.agent_id}'
    AND embedding IS NOT NULL
    AND valid_from <= '{query.as_of.isoformat()}'::TIMESTAMPTZ
    AND (valid_to IS NULL OR valid_to > '{query.as_of.isoformat()}'::TIMESTAMPTZ)
    AND recorded_at <= '{query.as_of.isoformat()}'::TIMESTAMPTZ
  ORDER BY embedding <=> '{vector}'::VECTOR(1024)
  LIMIT {limit}
)
SELECT nearest.*,
       CASE WHEN nearest.subject LIKE 'service:%' THEN '{query.service_id}'
            ELSE NULL END AS scoped_service_id,
       COALESCE((
         SELECT array_agg(episodic_event_id)
         FROM semantic_fact_provenance
         WHERE org_id = nearest.org_id AND fact_id = nearest.fact_id
           AND episodic_event_id IS NOT NULL
           AND role IN ('source', 'reinforcement')
       ), ARRAY[]::UUID[]) AS provenance_ids
FROM nearest
WHERE nearest.subject LIKE 'org:%'
   OR nearest.subject IN (
     SELECT 'service:' || name FROM services
     WHERE org_id = '{query.org_id}' AND service_id = '{query.service_id}'
     UNION ALL
     SELECT 'service:' || service_id::STRING FROM services
     WHERE org_id = '{query.org_id}' AND service_id = '{query.service_id}'
   )
""".strip()


def _procedural_sql(
    query: RecallQuery, vector: str, policy: RecallPolicy
) -> str:
    limit = policy.candidate_limit(query.k)
    return f"""
WITH nearest AS (
  SELECT runbook_id, org_id, agent_id, name, version, trigger_desc,
         preconditions, steps, postconditions, success_rate, usage_count,
         success_count, failure_count, last_used_at, created_at, created_by,
         applicable_service_tags, applicable_error_signatures,
         1 - (embedding <=> '{vector}'::VECTOR(1024)) AS similarity
  FROM procedural_memory
  WHERE org_id = '{query.org_id}' AND status = 'active'
    AND agent_id = '{query.agent_id}' AND embedding IS NOT NULL
    AND created_at <= '{query.as_of.isoformat()}'::TIMESTAMPTZ
  ORDER BY embedding <=> '{vector}'::VECTOR(1024)
  LIMIT {limit}
)
SELECT nearest.*,
       COALESCE((SELECT count(*) FROM runbook_provenance
                 WHERE runbook_id = nearest.runbook_id
                   AND role IN ('source', 'reinforcement')), 0)
         AS positive_provenance_count,
       COALESCE((SELECT count(*) FROM runbook_provenance
                 WHERE runbook_id = nearest.runbook_id
                   AND role = 'counterexample'), 0)
         AS counterexample_count,
       COALESCE((SELECT array_agg(episodic_event_id) FROM runbook_provenance
                 WHERE runbook_id = nearest.runbook_id
                   AND role IN ('source', 'reinforcement')
                   AND episodic_event_id IS NOT NULL), ARRAY[]::UUID[])
         AS provenance_ids
FROM nearest
""".strip()


def _unwrap(result: Any) -> Any:
    if not isinstance(result, dict) or "content" not in result:
        return result
    blocks = result.get("content") or []
    texts = [block.get("text", "") for block in blocks if isinstance(block, dict)]
    text = "".join(texts).strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RecallError("Managed MCP returned non-JSON tool content.") from exc


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
        memory_id=UUID(str(row["event_id"])),
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
            item
            for item in (
                _optional_uuid(row.get("action_id")),
                _optional_uuid(row.get("transaction_id")),
            )
            if item
        ),
    )


def _fact(row: dict[str, Any]) -> MemoryCandidate:
    content = f"{row.get('subject')} {row.get('predicate')}: {row.get('object')}"
    return MemoryCandidate(
        memory_id=UUID(str(row["fact_id"])),
        kind=MemoryKind.SEMANTIC,
        content=content,
        similarity=float(row.get("similarity") or 0.0),
        success_rate=float(row.get("confidence") or 0.0),
        metadata={
            "subject": row.get("subject"),
            "predicate": row.get("predicate"),
            "object": row.get("object"),
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
    steps = row.get("steps") or []
    if isinstance(steps, str):
        steps = json.loads(steps)
    return MemoryCandidate(
        memory_id=UUID(str(row["runbook_id"])),
        kind=MemoryKind.PROCEDURAL,
        content=str(row.get("trigger_desc") or row.get("name") or ""),
        similarity=float(row.get("similarity") or 0.0),
        success_rate=float(row.get("success_rate") or 0.0),
        runbook_id=UUID(str(row["runbook_id"])),
        steps=tuple(steps),
        metadata={
            "name": row.get("name"),
            "version": row.get("version"),
            "preconditions": row.get("preconditions"),
            "applicable_service_tags": row.get("applicable_service_tags"),
            "applicable_error_signatures": row.get("applicable_error_signatures"),
            "usage_count": int(row.get("usage_count") or 0),
            "success_count": int(row.get("success_count") or 0),
            "failure_count": int(row.get("failure_count") or 0),
            "positive_provenance_count": int(
                row.get("positive_provenance_count") or 0
            ),
            "counterexample_count": int(row.get("counterexample_count") or 0),
            "last_used_at": _text(row.get("last_used_at")),
            "learned_at": _text(row.get("created_at")),
        },
        org_id=_optional_uuid(row.get("org_id")),
        agent_id=_optional_uuid(row.get("agent_id")),
        recorded_at=_datetime(row.get("created_at")),
        provenance_ids=_uuid_tuple(row.get("provenance_ids")),
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _optional_uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)) if value else None


def _uuid_tuple(value: Any) -> tuple[UUID, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        value = json.loads(value)
    return tuple(UUID(str(item)) for item in value if item)


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _vector_literal(embedding: tuple[float, ...]) -> str:
    if len(embedding) != 1024:
        raise RecallError("Recall embedding must have exactly 1024 dimensions.")
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"
