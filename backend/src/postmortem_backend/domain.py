"""Typed contracts shared by the responder, adapters, and web console."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class ActionKind(StrEnum):
    ROLLBACK = "rollback"
    RESTART = "restart"
    SCALE = "scale"
    FEATURE_FLAG = "feature_flag"


class DecisionKind(StrEnum):
    REMEDIATE = "remediate_and_record"
    PROPOSE = "propose_action"
    ASK_HUMAN = "ask_human"
    ESCALATE = "escalate"


class OutcomeKind(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    NO_EFFECT = "no_effect"


class EventType(StrEnum):
    INCIDENT_RECEIVED = "incident.received"
    RECALL_STARTED = "recall.started"
    RECALL_COMPLETED = "recall.completed"
    REASONING_STARTED = "reasoning.started"
    DECISION_PROPOSED = "decision.proposed"
    ACTION_PROPOSED = "action.proposed"
    APPROVAL_REQUIRED = "approval.required"
    TRANSACTION_STARTED = "transaction.started"
    TRANSACTION_COMMITTED = "transaction.committed"
    TRANSACTION_ROLLED_BACK = "transaction.rolled_back"
    OUTCOME_RECORDED = "outcome.recorded"
    RESPONSE_COMPLETED = "response.completed"
    RESPONSE_FAILED = "response.failed"


@dataclass(frozen=True, slots=True)
class IncidentSignal:
    incident_id: UUID
    session_id: UUID
    org_id: UUID
    agent_id: UUID
    service_id: UUID
    severity: str
    summary: str
    error_signature: str | None = None
    service_tags: tuple[str, ...] = ()
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def recall_text(self) -> str:
        signature = f" Error signature: {self.error_signature}." if self.error_signature else ""
        tags = f" Service tags: {', '.join(self.service_tags)}." if self.service_tags else ""
        return (
            f"{self.severity} incident affecting service {self.service_id}. "
            f"{self.summary}.{signature}{tags}"
        )


@dataclass(frozen=True, slots=True)
class RecallQuery:
    org_id: UUID
    agent_id: UUID
    service_id: UUID
    text: str
    embedding: tuple[float, ...]
    k: int = 8
    service_tags: tuple[str, ...] = ()
    error_signature: str | None = None
    as_of: datetime = field(default_factory=lambda: datetime.now(UTC))
    cold_start: bool = False
    current_incident_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_id: UUID
    kind: MemoryKind
    content: str
    similarity: float
    success_rate: float = 0.0
    occurred_at: datetime | None = None
    service_id: UUID | None = None
    runbook_id: UUID | None = None
    steps: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    org_id: UUID | None = None
    agent_id: UUID | None = None
    confidence: float = 0.0
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    recorded_at: datetime | None = None
    provenance_ids: tuple[UUID, ...] = ()
    ranking_score: float = 0.0


@dataclass(frozen=True, slots=True)
class RecallBundle:
    episodes: tuple[MemoryCandidate, ...] = ()
    facts: tuple[MemoryCandidate, ...] = ()
    runbooks: tuple[MemoryCandidate, ...] = ()
    cold_start: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def all_candidates(self) -> tuple[MemoryCandidate, ...]:
        return self.episodes + self.facts + self.runbooks


@dataclass(frozen=True, slots=True)
class RemediationCommand:
    org_id: UUID
    agent_id: UUID
    incident_id: UUID
    session_id: UUID
    service_id: UUID
    action: ActionKind
    target_version: str
    cited_memory_id: UUID
    runbook_id: UUID | None
    rationale: str
    outcome_stub: str = "remediation_started"
    requires_human_approval: bool = False
    approved: bool = False
    idempotency_key: str | None = None

    def memory_text(self) -> str:
        return (
            f"{self.action.value} service {self.service_id} to {self.target_version}; "
            f"reason: {self.rationale}; expected outcome: {self.outcome_stub}"
        )

    def effective_idempotency_key(self) -> str:
        return self.idempotency_key or (
            f"{self.incident_id}:{self.action.value}:{self.service_id}:"
            f"{self.target_version}:{self.cited_memory_id}"
        )


@dataclass(frozen=True, slots=True)
class AgentDecision:
    kind: DecisionKind
    explanation: str
    cited_memory_id: UUID | None = None
    command: RemediationCommand | None = None
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class RemediationResult:
    action_id: UUID
    transaction_id: UUID
    event_id: UUID
    deploy_id: UUID
    committed_at: datetime
    incident_status: str = "mitigating"
    service_status: str = "recovering"


@dataclass(frozen=True, slots=True)
class OutcomeCommand:
    org_id: UUID
    agent_id: UUID
    incident_id: UUID
    service_id: UUID
    action_id: UUID
    outcome: OutcomeKind
    summary: str
    error_signature: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def memory_text(self) -> str:
        signature = (
            f" Error signature: {self.error_signature}."
            if self.error_signature
            else ""
        )
        return (
            f"Remediation action {self.action_id} for service {self.service_id} "
            f"finished with outcome {self.outcome.value}. {self.summary}.{signature}"
        )


@dataclass(frozen=True, slots=True)
class OutcomeResult:
    action_id: UUID
    transaction_id: UUID
    event_id: UUID
    incident_id: UUID
    outcome: OutcomeKind
    incident_status: str
    recorded_at: datetime
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class ResponseResult:
    incident_id: UUID
    decision: AgentDecision
    remediation: RemediationResult | None = None


def json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class AgentEvent:
    incident_id: UUID
    type: EventType
    stage: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return json_value(asdict(self))


@dataclass(frozen=True, slots=True)
class IncidentView:
    signal: IncidentSignal
    last_result: ResponseResult | None
    event_count: int

    def to_dict(self) -> dict[str, Any]:
        return json_value(asdict(self))
