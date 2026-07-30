"""Deterministic adapters used by local development and offline tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from math import sqrt
from threading import RLock
from uuid import UUID, uuid4

from ..domain import (
    ActionKind,
    AgentDecision,
    DecisionKind,
    IncidentSignal,
    MemoryCandidate,
    MemoryKind,
    OutcomeCommand,
    OutcomeKind,
    OutcomeResult,
    RecallBundle,
    RecallQuery,
    RemediationCommand,
    RemediationResult,
)
from ..errors import (
    ApprovalRequired,
    AtomicRemediationError,
    OutcomeConflict,
    OutcomeRecordingError,
    ProvenanceError,
    UnsupportedAction,
)

DEMO_RUNBOOK_ID = UUID("00000000-0000-0000-0000-000000000207")


class FakeEmbeddingAdapter:
    """Stable 1024-dimensional unit vectors without model credentials."""

    dimension = 1024

    def embed(self, text: str) -> tuple[float, ...]:
        encoded = text.encode("utf-8") or b"\0"
        vector = [0.0] * self.dimension
        for index, value in enumerate(encoded):
            vector[index % self.dimension] += (value + 1) / 256
        magnitude = sqrt(sum(value * value for value in vector))
        return tuple(value / magnitude for value in vector)


class FakeRecallAdapter:
    def __init__(self, bundle: RecallBundle | None = None) -> None:
        self.bundle = bundle or RecallBundle()
        self.queries: list[RecallQuery] = []

    def recall(self, query: RecallQuery) -> RecallBundle:
        self.queries.append(query)
        if query.cold_start:
            return RecallBundle(
                cold_start=True,
                diagnostics={"mode": "cold_start", "database_queries": 0},
            )
        return self.bundle


class FakeReasoningAdapter:
    """A strict rule-based reasoner exercising the same decision contract as Bedrock."""

    def decide(self, signal: IncidentSignal, memory: RecallBundle) -> AgentDecision:
        candidate = self._best_candidate(memory)
        if candidate is None:
            return AgentDecision(
                kind=DecisionKind.ESCALATE,
                explanation="No grounded memory matched this incident; page a human.",
                confidence=0.0,
            )

        metadata = candidate.metadata
        action = ActionKind(metadata.get("action", ActionKind.ROLLBACK.value))
        target_version = str(metadata.get("target_version", "last-known-good"))
        requires_approval = bool(metadata.get("requires_human_approval", False))
        runbook_id = (
            candidate.memory_id
            if candidate.kind is MemoryKind.PROCEDURAL
            else candidate.runbook_id
        )
        command = RemediationCommand(
            org_id=signal.org_id,
            agent_id=signal.agent_id,
            incident_id=signal.incident_id,
            session_id=signal.session_id,
            service_id=signal.service_id,
            action=action,
            target_version=target_version,
            cited_memory_id=candidate.memory_id,
            runbook_id=runbook_id,
            rationale=(
                f"Matched {candidate.kind.value} memory {candidate.memory_id} "
                f"with similarity {candidate.similarity:.3f}."
            ),
            requires_human_approval=requires_approval,
        )
        return AgentDecision(
            kind=DecisionKind.REMEDIATE,
            explanation=command.rationale,
            cited_memory_id=candidate.memory_id,
            command=command,
            confidence=candidate.similarity,
        )

    @staticmethod
    def _best_candidate(memory: RecallBundle) -> MemoryCandidate | None:
        candidates = tuple(
            candidate
            for candidate in (memory.runbooks or memory.episodes)
            if candidate.metadata.get("actionable", True)
        )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (item.similarity * 0.7) + (item.success_rate * 0.3),
        )


class FakeAtomicRemediationStore:
    """Copy-on-write transaction model with deterministic failure injection."""

    SUPPORTED_ACTIONS = frozenset({ActionKind.ROLLBACK, ActionKind.RESTART})

    def __init__(self, *, auto_seed: bool = False) -> None:
        self.services: dict[UUID, dict[str, object]] = {}
        self.incidents: dict[UUID, dict[str, object]] = {}
        self.provenance: set[UUID] = set()
        self.deploys: dict[UUID, dict[str, object]] = {}
        self.episodes: dict[UUID, dict[str, object]] = {}
        self.remediation_actions: dict[UUID, dict[str, object]] = {}
        self.runbook_usage: dict[UUID, int] = {}
        self.fail_at: str | None = None
        self.commit_count = 0
        self.rollback_count = 0
        self.outcome_commit_count = 0
        self.outcome_rollback_count = 0
        self.auto_seed = auto_seed
        self._lock = RLock()

    def seed(
        self,
        *,
        service_id: UUID,
        incident_id: UUID,
        cited_memory_id: UUID,
        org_id: UUID,
    ) -> None:
        self.services[service_id] = {
            "org_id": org_id,
            "status": "degraded",
            "current_deploy_id": None,
        }
        self.incidents[incident_id] = {
            "org_id": org_id,
            "service_id": service_id,
            "status": "open",
            "runbook_id": None,
        }
        self.provenance.add(cited_memory_id)

    def snapshot(self) -> dict[str, object]:
        return deepcopy(
            {
                "services": self.services,
                "incidents": self.incidents,
                "deploys": self.deploys,
                "episodes": self.episodes,
                "remediation_actions": self.remediation_actions,
                "runbook_usage": self.runbook_usage,
            }
        )

    def remediate_and_record(
        self, command: RemediationCommand, embedding: tuple[float, ...]
    ) -> RemediationResult:
        self._validate(command, embedding)
        with self._lock:
            if self.auto_seed:
                self.services.setdefault(
                    command.service_id,
                    {
                        "org_id": command.org_id,
                        "status": "degraded",
                        "current_deploy_id": None,
                    },
                )
                self.incidents.setdefault(
                    command.incident_id,
                    {
                        "org_id": command.org_id,
                        "service_id": command.service_id,
                        "status": "open",
                        "runbook_id": None,
                    },
                )
                self.provenance.add(command.cited_memory_id)
            staged = self.snapshot()
            deploy_id = uuid4()
            event_id = uuid4()
            action_id = uuid4()
            transaction_id = uuid4()
            now = datetime.now(UTC)
            try:
                service = staged["services"].get(command.service_id)
                incident = staged["incidents"].get(command.incident_id)
                if not service or service["org_id"] != command.org_id:
                    raise AtomicRemediationError("Target service is absent or outside the org scope.")
                if (
                    not incident
                    or incident["org_id"] != command.org_id
                    or incident["service_id"] != command.service_id
                ):
                    raise AtomicRemediationError(
                        "Target incident is absent or does not match the service."
                    )
                if command.cited_memory_id not in self.provenance:
                    raise ProvenanceError("Cited memory/runbook does not exist.")

                staged["deploys"][deploy_id] = {
                    "org_id": command.org_id,
                    "service_id": command.service_id,
                    "version": command.target_version,
                    "action": command.action.value,
                    "deployed_by": f"agent:{command.agent_id}",
                    "status": "completed",
                }
                service["status"] = "recovering"
                service["current_deploy_id"] = deploy_id
                incident["status"] = "mitigating"
                incident["runbook_id"] = command.runbook_id
                if self.fail_at == "after_operational_write":
                    raise AtomicRemediationError("Injected failure after operational mutation.")

                staged["episodes"][event_id] = {
                    "org_id": command.org_id,
                    "agent_id": command.agent_id,
                    "incident_id": command.incident_id,
                    "session_id": command.session_id,
                    "service_id": command.service_id,
                    "event_type": "action",
                    "content": command.memory_text(),
                    "metadata": {
                        "deploy_id": str(deploy_id),
                        "cited_memory_id": str(command.cited_memory_id),
                        "outcome": command.outcome_stub,
                    },
                    "runbook_id": command.runbook_id,
                    "importance": 0.9,
                    "embedding": embedding,
                }
                if self.fail_at == "during_memory_write":
                    raise AtomicRemediationError("Injected failure during episodic append.")

                staged["remediation_actions"][action_id] = {
                    "org_id": command.org_id,
                    "incident_id": command.incident_id,
                    "action_type": f"{command.action.value}_deploy"
                    if command.action is ActionKind.ROLLBACK
                    else "restart_service",
                    "target_id": command.service_id,
                    "params": {"target_version": command.target_version},
                    "applied_by": f"agent:{command.agent_id}",
                    "outcome": "success",
                    "memory_ref": event_id,
                    "transaction_id": transaction_id,
                    "idempotency_key": command.effective_idempotency_key(),
                }
                if self.fail_at == "during_audit_write":
                    raise AtomicRemediationError("Injected failure during action audit append.")

                if command.runbook_id is not None:
                    staged["runbook_usage"][command.runbook_id] = (
                        staged["runbook_usage"].get(command.runbook_id, 0) + 1
                    )

                self.services = staged["services"]
                self.incidents = staged["incidents"]
                self.deploys = staged["deploys"]
                self.episodes = staged["episodes"]
                self.remediation_actions = staged["remediation_actions"]
                self.runbook_usage = staged["runbook_usage"]
                self.commit_count += 1
            except Exception:
                self.rollback_count += 1
                raise

        return RemediationResult(
            action_id=action_id,
            transaction_id=transaction_id,
            deploy_id=deploy_id,
            event_id=event_id,
            committed_at=now,
        )

    def _validate(
        self, command: RemediationCommand, embedding: tuple[float, ...]
    ) -> None:
        if command.action not in self.SUPPORTED_ACTIONS:
            raise UnsupportedAction(f"{command.action.value} is not implemented in Phase 1.")
        if command.requires_human_approval and not command.approved:
            raise ApprovalRequired("This remediation requires explicit SRE approval.")
        if len(embedding) != 1024:
            raise AtomicRemediationError("Episodic embedding must have exactly 1024 dimensions.")

    def record_outcome(
        self, command: OutcomeCommand, embedding: tuple[float, ...]
    ) -> OutcomeResult:
        if len(embedding) != 1024:
            raise OutcomeRecordingError(
                "Outcome embedding must have exactly 1024 dimensions."
            )
        with self._lock:
            staged = self.snapshot()
            try:
                incident = staged["incidents"].get(command.incident_id)
                action = staged["remediation_actions"].get(command.action_id)
                if (
                    not incident
                    or incident["org_id"] != command.org_id
                    or incident["service_id"] != command.service_id
                    or not action
                    or action["org_id"] != command.org_id
                    or action["incident_id"] != command.incident_id
                    or action["target_id"] != command.service_id
                ):
                    raise ProvenanceError(
                        "Outcome action does not belong to the org, incident, and service."
                    )

                existing = next(
                    (
                        (event_id, event)
                        for event_id, event in staged["episodes"].items()
                        if event.get("event_type") == "outcome"
                        and event.get("metadata", {}).get("action_id")
                        == str(command.action_id)
                    ),
                    None,
                )
                if existing is not None:
                    event_id, event = existing
                    recorded = OutcomeKind(event["metadata"]["outcome"])
                    if recorded is not command.outcome:
                        raise OutcomeConflict(
                            f"Outcome already recorded as {recorded.value}."
                        )
                    return OutcomeResult(
                        action_id=command.action_id,
                        transaction_id=action["transaction_id"],
                        event_id=event_id,
                        incident_id=command.incident_id,
                        outcome=recorded,
                        incident_status=str(incident["status"]),
                        recorded_at=event["created_at"],
                        idempotent_replay=True,
                    )

                action["outcome"] = command.outcome.value
                if self.fail_at == "outcome_after_action_update":
                    raise OutcomeRecordingError(
                        "Injected failure after outcome action update."
                    )

                if command.outcome is OutcomeKind.SUCCESS:
                    incident["status"] = "resolved"
                    incident["resolved_at"] = command.observed_at
                event_id = uuid4()
                staged["episodes"][event_id] = {
                    "org_id": command.org_id,
                    "agent_id": command.agent_id,
                    "incident_id": command.incident_id,
                    "service_id": command.service_id,
                    "event_type": "outcome",
                    "content": command.memory_text(),
                    "metadata": {
                        "outcome": command.outcome.value,
                        "action_id": str(command.action_id),
                        "transaction_id": str(action["transaction_id"]),
                        "action_type": action["action_type"],
                        "params": deepcopy(action["params"]),
                        "service_id": str(command.service_id),
                        "error_signature": command.error_signature,
                        "consolidation_ready": True,
                        "source": "responder_outcome",
                    },
                    "runbook_id": incident.get("runbook_id"),
                    "importance": 0.95,
                    "embedding": embedding,
                    "occurred_at": command.observed_at,
                    "created_at": command.observed_at,
                }
                if self.fail_at == "outcome_during_event_write":
                    raise OutcomeRecordingError(
                        "Injected failure during outcome episode append."
                    )

                self.incidents = staged["incidents"]
                self.remediation_actions = staged["remediation_actions"]
                self.episodes = staged["episodes"]
                self.outcome_commit_count += 1
            except Exception:
                self.outcome_rollback_count += 1
                raise

        return OutcomeResult(
            action_id=command.action_id,
            transaction_id=action["transaction_id"],
            event_id=event_id,
            incident_id=command.incident_id,
            outcome=command.outcome,
            incident_status=str(incident["status"]),
            recorded_at=command.observed_at,
        )


def approved(command: RemediationCommand) -> RemediationCommand:
    """Convenience helper for tests and API orchestration."""

    return replace(command, approved=True)


def phase_one_demo_memory() -> RecallBundle:
    """Seeded prior success used by the credential-free local vertical slice."""

    runbook = MemoryCandidate(
        memory_id=DEMO_RUNBOOK_ID,
        kind=MemoryKind.PROCEDURAL,
        content=(
            "A post-deploy 5xx spike was resolved by rolling back the canary "
            "to the last-known-good version."
        ),
        similarity=0.94,
        success_rate=0.90,
        runbook_id=DEMO_RUNBOOK_ID,
        metadata={
            "source_case_id": "CASE-1878",
            "successful_action": "Rollback canary",
            "action": ActionKind.ROLLBACK.value,
            "target_version": "1.4.2",
            "requires_human_approval": False,
        },
    )
    return RecallBundle(runbooks=(runbook,))
