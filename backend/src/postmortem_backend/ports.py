"""Hexagonal ports for cloud services and persistence."""

from __future__ import annotations

from typing import Protocol

from .domain import (
    AgentDecision,
    AgentEvent,
    IncidentSignal,
    OutcomeCommand,
    OutcomeResult,
    RecallBundle,
    RecallQuery,
    RemediationCommand,
    RemediationResult,
)


class EmbeddingPort(Protocol):
    def embed(self, text: str) -> tuple[float, ...]:
        """Return a normalized 1024-dimensional embedding."""


class RecallPort(Protocol):
    def recall(self, query: RecallQuery) -> RecallBundle:
        """Recall scoped episodic, semantic, and procedural memory."""


class ReasoningPort(Protocol):
    def decide(self, signal: IncidentSignal, memory: RecallBundle) -> AgentDecision:
        """Choose a grounded control decision."""


class AtomicRemediationPort(Protocol):
    def remediate_and_record(
        self, command: RemediationCommand, embedding: tuple[float, ...]
    ) -> RemediationResult:
        """Commit the operational action and episodic record atomically."""


class OutcomeRecordingPort(Protocol):
    def record_outcome(
        self, command: OutcomeCommand, embedding: tuple[float, ...]
    ) -> OutcomeResult:
        """Commit verified action outcome, incident state, and outcome episode atomically."""


class EventSink(Protocol):
    def publish(self, event: AgentEvent) -> None:
        """Publish an event for SSE and durable/observability adapters."""
