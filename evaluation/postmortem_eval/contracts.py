from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from postmortem_sim.models import IncidentObservation


@dataclass(frozen=True)
class ActionPlan:
    action_type: str
    target_service: str
    params: dict[str, Any]


@dataclass(frozen=True)
class RetrievalHit:
    memory_id: str
    score: float


@dataclass(frozen=True)
class ResponderDecision:
    actions: tuple[ActionPlan, ...]
    retrieved: tuple[RetrievalHit, ...] = ()
    token_proxy: int = 0
    abstained: bool = False
    authorized_memory_id: str | None = None
    decision_seconds: int = 0


class Responder(Protocol):
    name: str

    def decide(self, observation: IncidentObservation) -> ResponderDecision:
        """Return an ordered remediation plan from agent-visible state only."""
