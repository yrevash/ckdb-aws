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
    # Seconds spent deciding. Deterministic responders report the fixed
    # non-informative 0 (see responders.DECISION_LATENCY_NOT_MEASURED); the
    # real-agent responder reports measured wall-clock, hence float.
    decision_seconds: float = 0

    # Populated only by a real model-backed responder (real_agent.py). The
    # deterministic arms leave these at zero and report ``token_proxy``
    # instead -- a proxy is not a token count and the two must never be
    # compared or summed (Reality Charter R1/R9).
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str | None = None
    # Set when the model returned a plan the simulator could not execute at
    # all (unknown action type, missing required param). A real decision
    # -quality signal, not a harness error.
    invalid_plan: bool = False


class Responder(Protocol):
    name: str

    def decide(self, observation: IncidentObservation) -> ResponderDecision:
        """Return an ordered remediation plan from agent-visible state only."""


@dataclass(frozen=True)
class TemporalValidityRecord:
    """One temporal-drift incident's outcome: did the memory arm apply the
    fix that is actually valid at the incident's decision time, or a fix
    that has since been superseded by an environment change?

    ``expected_memory_id`` is derived purely from each candidate memory's
    ``valid_from``/``valid_to`` window evaluated at ``observed_at`` -- never
    from the responder's own choice -- so this record is an independent
    check, not a restatement of what the responder did.
    """

    scenario_id: str
    family_id: str
    variant_id: str
    observed_at: str | None
    authorized_memory_id: str | None
    expected_memory_id: str | None
    applied_currently_valid_fix: bool
    applied_stale_fact: bool
    resolved: bool
    mttr_seconds: int | None
