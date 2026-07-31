"""Perceive → Recall → Reason → Act+Record application service."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID

from .domain import (
    AgentEvent,
    DecisionKind,
    EventType,
    IncidentSignal,
    IncidentView,
    OutcomeCommand,
    OutcomeResult,
    RecallQuery,
    ResponseResult,
)
from .guardrails.allowlist import (
    authorize_action,
    enforce_tool_allowlist,
    resolve_decision_tool,
)
from .guardrails.injection import sanitize_signal
from .guardrails.provenance import require_grounded_action
from .ports import (
    AtomicRemediationPort,
    EmbeddingPort,
    EventSink,
    OutcomeRecordingPort,
    ReasoningPort,
    RecallPort,
)


class IncidentRegistry:
    """Small process-local read model; CockroachDB remains authoritative for incident state."""

    def __init__(self) -> None:
        self._signals: dict[UUID, IncidentSignal] = {}
        self._results: dict[UUID, ResponseResult] = {}
        self._lock = RLock()

    def received(self, signal: IncidentSignal) -> None:
        with self._lock:
            self._signals[signal.incident_id] = signal

    def completed(self, result: ResponseResult) -> None:
        with self._lock:
            self._results[result.incident_id] = result

    def view(self, incident_id: UUID, event_count: int) -> IncidentView | None:
        with self._lock:
            signal = self._signals.get(incident_id)
            if signal is None:
                return None
            return IncidentView(
                signal=signal,
                last_result=self._results.get(incident_id),
                event_count=event_count,
            )


class ResponderService:
    def __init__(
        self,
        *,
        embedder: EmbeddingPort,
        recall: RecallPort,
        reasoner: ReasoningPort,
        remediation: AtomicRemediationPort,
        events: EventSink,
        registry: IncidentRegistry | None = None,
    ) -> None:
        self._embedder = embedder
        self._recall = recall
        self._reasoner = reasoner
        self._remediation = remediation
        self._events = events
        self.registry = registry or IncidentRegistry()

    def handle(
        self,
        signal: IncidentSignal,
        *,
        approved: bool = False,
        approver: str | None = None,
    ) -> ResponseResult:
        transaction_started = False
        # Prompt-injection defense (T1/R6): every untrusted free-text field of the
        # inbound signal is scrubbed + screened before it can reach recall or the
        # model. An injection attempt fails the turn closed here.
        signal = sanitize_signal(signal)
        self.registry.received(signal)
        self._event(
            signal,
            EventType.INCIDENT_RECEIVED,
            "perceive",
            f"Received {signal.severity} incident.",
            {
                "service": str(signal.service_id),
                "service_id": str(signal.service_id),
                "severity": signal.severity,
                "status": "open",
                "summary": signal.summary,
            },
        )
        try:
            self._event(
                signal,
                EventType.RECALL_STARTED,
                "recall",
                "Embedding incident signature.",
                {"query_summary": signal.recall_text(), "duration_ms": 0, "results": []},
            )
            query_embedding = self._embedder.embed(signal.recall_text())
            memory = self._recall.recall(
                RecallQuery(
                    org_id=signal.org_id,
                    agent_id=signal.agent_id,
                    service_id=signal.service_id,
                    text=signal.recall_text(),
                    embedding=query_embedding,
                    service_tags=signal.service_tags,
                    error_signature=signal.error_signature,
                    as_of=signal.observed_at,
                    cold_start=bool(signal.metadata.get("cold_start", False)),
                    current_incident_id=signal.incident_id,
                )
            )
            self._event(
                signal,
                EventType.RECALL_COMPLETED,
                "recall",
                "Recalled scoped organizational memory.",
                {
                    "episodes": len(memory.episodes),
                    "facts": len(memory.facts),
                    "runbooks": len(memory.runbooks),
                    "cold_start": memory.cold_start,
                    "diagnostics": memory.diagnostics,
                    "provider": (
                        "cold-start"
                        if memory.cold_start
                        else memory.diagnostics.get("provider", "c-spann+mcp")
                    ),
                    "rejected_count": _rejected_count(memory.diagnostics),
                    "memory_ids": [str(item.memory_id) for item in memory.all_candidates],
                    "query_summary": signal.recall_text(),
                    "duration_ms": 0,
                    "results": [
                        {
                            "memoryId": str(item.memory_id),
                            "memoryKind": item.kind.value,
                            "sourceCaseId": str(
                                item.metadata.get("source_case_id", signal.incident_id)
                            ),
                            "summary": item.content,
                            "similarity": item.similarity,
                            "accepted": True,
                            "confidence": item.confidence,
                            "successRate": item.success_rate,
                            "score": _console_score(item),
                            "provenance": [
                                str(value) for value in item.provenance_ids
                            ],
                            **(
                                {"runbookId": str(item.runbook_id)}
                                if item.runbook_id
                                else {}
                            ),
                            **(
                                {
                                    "successfulAction": str(
                                        item.metadata["successful_action"]
                                    )
                                }
                                if item.metadata.get("successful_action")
                                else {}
                            ),
                            "scope": {
                                "service": str(item.service_id or signal.service_id),
                                "tenant": str(signal.org_id),
                            },
                            "validFrom": str(
                                item.metadata.get(
                                    "valid_from",
                                    (
                                        item.occurred_at.isoformat()
                                        if item.occurred_at
                                        else signal.observed_at.isoformat()
                                    ),
                                )
                            ),
                            "learnedAt": str(
                                item.metadata.get(
                                    "learned_at",
                                    (
                                        item.occurred_at.isoformat()
                                        if item.occurred_at
                                        else signal.observed_at.isoformat()
                                    ),
                                )
                            ),
                        }
                        for item in memory.all_candidates
                    ],
                },
            )
            self._event(
                signal,
                EventType.REASONING_STARTED,
                "reason",
                "Selecting a grounded response.",
            )
            decision = self._reasoner.decide(signal, memory)
            self._event(
                signal,
                EventType.DECISION_PROPOSED,
                "reason",
                decision.explanation,
                {
                    "decision": decision.kind.value,
                    "confidence": decision.confidence,
                    "cited_memory_id": (
                        str(decision.cited_memory_id) if decision.cited_memory_id else None
                    ),
                    "cited_runbook_id": (
                        str(decision.command.runbook_id)
                        if decision.command and decision.command.runbook_id
                        else None
                    ),
                },
            )

            remediation_result = None
            if decision.kind is DecisionKind.REMEDIATE and decision.command is not None:
                command = replace(decision.command, approved=approved)
                decision = replace(decision, command=command)
                # Tool allowlist (R3): the agent may only drive an allowlisted tool.
                enforce_tool_allowlist(resolve_decision_tool(decision.kind))
                # Provenance gate (R4): reject an ungrounded action BEFORE execution;
                # the citation must resolve to a memory Recall actually surfaced.
                require_grounded_action(
                    command,
                    recalled_ids=[item.memory_id for item in memory.all_candidates],
                )
                self._event(
                    signal,
                    EventType.ACTION_PROPOSED,
                    "act",
                    "Grounded remediation proposed.",
                    {
                        "action": command.action.value,
                        "action_id": command.effective_idempotency_key(),
                        "tool": "remediate_and_record",
                        "target": str(command.service_id),
                        "cited_memory_id": str(command.cited_memory_id),
                        "requires_approval": command.requires_human_approval,
                        "arguments": {
                            "action": command.action.value,
                            "target_version": command.target_version,
                        },
                    },
                )
                if command.requires_human_approval and not command.approved:
                    self._event(
                        signal,
                        EventType.APPROVAL_REQUIRED,
                        "act",
                        "Remediation is paused for explicit SRE approval.",
                        {
                            "action": command.action.value,
                            "tool": "remediate_and_record",
                            "target": str(command.service_id),
                            "cited_memory_id": str(command.cited_memory_id),
                            "arguments": {
                                "action": command.action.value,
                                "target_version": command.target_version,
                            },
                        },
                    )
                else:
                    # Destructive / high-blast-radius gate (R5): refuse an
                    # irreversible action without explicit, named human approval;
                    # record the authorization decision (approver + reason) so the
                    # audit trail attributes every executed high-risk action.
                    approval_record = authorize_action(
                        command,
                        human_approved=command.approved,
                        approver=approver,
                    )
                    self._event(
                        signal,
                        EventType.TRANSACTION_STARTED,
                        "act+record",
                        "Starting atomic operational action and episodic append.",
                        {
                            "action": command.action.value,
                            "transaction_id": str(command.incident_id),
                            "authorization": approval_record.to_dict(),
                        },
                    )
                    transaction_started = True
                    action_embedding = self._embedder.embed(command.memory_text())
                    remediation_result = self._remediation.remediate_and_record(
                        command, action_embedding
                    )
                    self._event(
                        signal,
                        EventType.TRANSACTION_COMMITTED,
                        "act+record",
                        "Operational action and memory committed together.",
                        {
                            "action_id": str(remediation_result.action_id),
                            "transaction_id": str(remediation_result.transaction_id),
                            "deploy_id": str(remediation_result.deploy_id),
                            "event_id": str(remediation_result.event_id),
                            "committed_at": remediation_result.committed_at.isoformat(),
                        },
                    )

            result = ResponseResult(
                incident_id=signal.incident_id,
                decision=decision,
                remediation=remediation_result,
            )
            self.registry.completed(result)
            self._event(
                signal,
                EventType.RESPONSE_COMPLETED,
                "record",
                "Responder turn completed.",
                {
                    "remediated": remediation_result is not None,
                    "memory_id": (
                        str(remediation_result.event_id) if remediation_result else None
                    ),
                    "summary": (
                        "Action outcome recorded atomically with operational state."
                        if remediation_result
                        else decision.explanation
                    ),
                    "freshness_ms": 0,
                    "stale_reads_observed": 0,
                },
            )
            return result
        except Exception as exc:
            event_type = (
                EventType.TRANSACTION_ROLLED_BACK
                if transaction_started
                else EventType.RESPONSE_FAILED
            )
            self._event(
                signal,
                event_type,
                "act+record" if event_type is EventType.TRANSACTION_ROLLED_BACK else "response",
                "Responder failed safely; no partial remediation was recorded.",
                {"error_type": type(exc).__name__, "error": str(exc)},
            )
            raise

    def _event(
        self,
        signal: IncidentSignal,
        type_: EventType,
        stage: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> None:
        self._events.publish(
            AgentEvent(
                incident_id=signal.incident_id,
                type=type_,
                stage=stage,
                message=message,
                data=data or {},
            )
        )


class OutcomeService:
    """Verifies and records remediation results for consolidation."""

    def __init__(
        self,
        *,
        embedder: EmbeddingPort,
        outcomes: OutcomeRecordingPort,
        events: EventSink,
    ) -> None:
        self._embedder = embedder
        self._outcomes = outcomes
        self._events = events

    def record(self, command: OutcomeCommand) -> OutcomeResult:
        embedding = self._embedder.embed(command.memory_text())
        result = self._outcomes.record_outcome(command, embedding)
        freshness_ms = max(
            int(
                (
                    datetime.now(UTC) - result.recorded_at
                ).total_seconds()
                * 1000
            ),
            0,
        )
        if not result.idempotent_replay:
            self._events.publish(
                AgentEvent(
                    incident_id=command.incident_id,
                    type=EventType.OUTCOME_RECORDED,
                    stage="record",
                    message=(
                        f"Recorded {command.outcome.value} outcome for "
                        f"action {command.action_id}."
                    ),
                    data={
                        "memory_id": str(result.event_id),
                        "memory_kind": "episodic",
                        "summary": command.summary,
                        "freshness_ms": freshness_ms,
                        "stale_reads_observed": 0,
                        "action_id": str(command.action_id),
                        "transaction_id": str(result.transaction_id),
                        "outcome": command.outcome.value,
                        "incident_status": result.incident_status,
                        "idempotent_replay": result.idempotent_replay,
                    },
                )
            )
        return result


def _rejected_count(diagnostics: dict[str, object]) -> int:
    candidates = diagnostics.get("candidate_counts")
    eligible = diagnostics.get("eligible_counts")
    if not isinstance(candidates, dict) or not isinstance(eligible, dict):
        return 0
    return max(
        sum(int(value) for value in candidates.values())
        - sum(int(value) for value in eligible.values()),
        0,
    )


def _console_score(item: object) -> dict[str, float]:
    metadata = getattr(item, "metadata", {})
    components = metadata.get("rank_components", {}) if isinstance(metadata, dict) else {}
    similarity = float(getattr(item, "similarity", 0.0))
    return {
        "vector": float(components.get("similarity", similarity)),
        "scope": float(components.get("scope", 0.0)),
        "freshness": float(components.get("recency", 0.0)),
        "outcome": float(
            components.get(
                "success_rate",
                components.get("successful_outcome", 0.0),
            )
        ),
        "composite": float(getattr(item, "ranking_score", similarity)),
    }
