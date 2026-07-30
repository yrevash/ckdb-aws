"""Stable backend-to-console transport mapping."""

from __future__ import annotations

import json
from typing import Any, Literal

from .domain import AgentEvent, EventType

ConsoleRegion = Literal["us-east", "us-west", "eu-west"]


def console_event(
    event: AgentEvent,
    *,
    sequence: int,
    agent_id: str = "responder",
    agent_region: ConsoleRegion = "us-east",
) -> dict[str, Any]:
    """Map internal events to the exact discriminated union in `web/lib/events.ts`."""

    event_type, payload = _payload(event)
    return {
        "id": str(event.event_id),
        "sequence": sequence,
        "occurredAt": event.occurred_at.isoformat(),
        "caseId": str(event.incident_id),
        "agent": {"id": agent_id, "region": agent_region},
        "type": event_type,
        "payload": payload,
    }


def sse_message(
    event: AgentEvent,
    *,
    sequence: int,
    agent_id: str = "responder",
    agent_region: ConsoleRegion = "us-east",
) -> str:
    envelope = console_event(
        event,
        sequence=sequence,
        agent_id=agent_id,
        agent_region=agent_region,
    )
    return (
        f"id: {envelope['id']}\n"
        f"event: {envelope['type']}\n"
        f"data: {json.dumps(envelope, separators=(',', ':'), default=str)}\n\n"
    )


def console_region(aws_region: str) -> ConsoleRegion:
    if aws_region.startswith("us-west"):
        return "us-west"
    if aws_region.startswith("eu-west"):
        return "eu-west"
    return "us-east"


def _payload(event: AgentEvent) -> tuple[str, dict[str, Any]]:
    data = event.data
    if event.type is EventType.INCIDENT_RECEIVED:
        return (
            "incident",
            {
                "service": str(data.get("service", data.get("service_id", "unknown"))),
                "severity": _severity(data.get("severity")),
                "status": "open",
                "summary": str(data.get("summary", event.message)),
            },
        )
    if event.type in {EventType.RECALL_STARTED, EventType.RECALL_COMPLETED}:
        return (
            "recall",
            {
                "querySummary": str(data.get("query_summary", event.message)),
                "provider": str(data.get("provider", "c-spann+mcp")),
                "mode": "cold" if data.get("cold_start") else "memory",
                "durationMs": int(data.get("duration_ms", 0)),
                "rejectedCount": int(data.get("rejected_count", 0)),
                "results": list(data.get("results", [])),
            },
        )
    if event.type in {
        EventType.REASONING_STARTED,
        EventType.DECISION_PROPOSED,
        EventType.RESPONSE_FAILED,
    }:
        cited_memory = data.get("cited_memory_id")
        cited_runbook = data.get("cited_runbook_id")
        return (
            "reason",
            {
                "message": str(data.get("message", event.message)),
                "citedMemoryIds": [str(cited_memory)] if cited_memory else [],
                "citedRunbookIds": [str(cited_runbook)] if cited_runbook else [],
            },
        )
    if event.type in {EventType.ACTION_PROPOSED, EventType.APPROVAL_REQUIRED}:
        cited_memory = str(data.get("cited_memory_id", event.event_id))
        return (
            "act",
            {
                "actionId": str(data.get("action_id", event.event_id)),
                "status": "proposed",
                "tool": str(data.get("tool", "remediate_and_record")),
                "arguments": dict(data.get("arguments", {})),
                "target": str(data.get("target", "unknown")),
                "requiresApproval": bool(
                    data.get(
                        "requires_approval",
                        event.type is EventType.APPROVAL_REQUIRED,
                    )
                ),
                "citedMemoryId": cited_memory,
            },
        )
    if event.type in {
        EventType.TRANSACTION_STARTED,
        EventType.TRANSACTION_COMMITTED,
        EventType.TRANSACTION_ROLLED_BACK,
    }:
        state = {
            EventType.TRANSACTION_STARTED: "begun",
            EventType.TRANSACTION_COMMITTED: "committed",
            EventType.TRANSACTION_ROLLED_BACK: "rolled_back",
        }[event.type]
        payload: dict[str, Any] = {
            "transactionId": str(data.get("transaction_id", event.event_id)),
            "state": state,
            "statements": list(data.get("statements", _transaction_statements())),
        }
        if state == "committed":
            payload["committedAt"] = str(data.get("committed_at", event.occurred_at.isoformat()))
        return "transaction", payload
    if event.type in {
        EventType.RESPONSE_COMPLETED,
        EventType.OUTCOME_RECORDED,
    } and data.get("memory_id"):
        return (
            "record",
            {
                "memoryId": str(data["memory_id"]),
                "memoryKind": str(data.get("memory_kind", "episodic")),
                "summary": str(data.get("summary", event.message)),
                "freshnessMs": int(data.get("freshness_ms", 0)),
                "staleReadsObserved": int(data.get("stale_reads_observed", 0)),
            },
        )
    return (
        "reason",
        {
            "message": event.message,
            "citedMemoryIds": [],
            "citedRunbookIds": [],
        },
    )


def _severity(value: Any) -> str:
    normalized = str(value or "SEV-3").upper().replace("SEV", "SEV-")
    normalized = normalized.replace("--", "-")
    return normalized if normalized in {"SEV-1", "SEV-2", "SEV-3"} else "SEV-3"


def _transaction_statements() -> list[dict[str, str]]:
    return [
        {
            "operation": "INSERT",
            "target": "deploys",
            "role": "action",
            "summary": "Record and apply the operational deployment action.",
        },
        {
            "operation": "UPDATE",
            "target": "services, incidents",
            "role": "action",
            "summary": "Move the service and incident into recovery.",
        },
        {
            "operation": "INSERT",
            "target": "episodic_events",
            "role": "memory",
            "summary": "Append the grounded action memory.",
        },
        {
            "operation": "INSERT",
            "target": "remediation_actions",
            "role": "audit",
            "summary": "Link action provenance to the episodic memory.",
        },
    ]
