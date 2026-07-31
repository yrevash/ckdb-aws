from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any

from postmortem_sim.models import IncidentObservation

from .contracts import ActionPlan, ResponderDecision, RetrievalHit


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
DEFAULT_MATCH_THRESHOLD = 0.15

# Decision latency (seconds spent "thinking" before acting) is a property of
# the *reasoning agent*, not of this deterministic mechanism harness. Under the
# Reality Charter (R7) it may only be measured by the real Bedrock agent, so the
# deterministic responders below report a single fixed, non-informative value and
# never a per-occurrence ramp. See docs/reality/00-reality-charter.md.
DECISION_LATENCY_NOT_MEASURED = 0

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a",
    "after",
    "and",
    "are",
    "checkout",
    "causes",
    "error",
    "errors",
    "incident",
    "is",
    "payment",
    "service",
    "the",
    "to",
    "with",
}


def tokenize(text: str) -> frozenset[str]:
    return frozenset(TOKEN_RE.findall(text.lower())) - STOP_WORDS


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    family_id: str
    text: str
    actions: tuple[ActionPlan, ...]


class ProceduralMemoryResponder:
    name = "with_memory"

    def __init__(
        self,
        corpus_path: Path | str = FIXTURE_ROOT / "memory_corpus.json",
        *,
        retrieval_k: int = 10,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        payload = json.loads(Path(corpus_path).read_text())
        self.records = tuple(
            MemoryRecord(
                memory_id=item["memory_id"],
                family_id=item["family_id"],
                text=item["text"],
                actions=tuple(
                    ActionPlan(
                        action_type=action["action_type"],
                        target_service=action["target_service"],
                        params=action.get("params", {}),
                    )
                    for action in item.get("actions", [])
                ),
            )
            for item in payload["memories"]
        )
        self.retrieval_k = retrieval_k
        self.match_threshold = match_threshold

    def decide(self, observation: IncidentObservation) -> ResponderDecision:
        query = tokenize(f"{observation.title} {observation.signal}")
        ranked = sorted(
            (
                (
                    self._similarity(query, tokenize(record.text)),
                    record.memory_id,
                    record,
                )
                for record in self.records
            ),
            key=lambda item: (-item[0], item[1]),
        )
        top = ranked[: self.retrieval_k]
        hits = tuple(
            RetrievalHit(memory_id=memory_id, score=round(score, 6))
            for score, memory_id, _ in top
        )
        retrieval_tokens = sum(len(tokenize(record.text)) for _, _, record in top)
        base_tokens = 220 + len(query) * 3 + retrieval_tokens

        # Authorization comes purely from the real ranking + similarity
        # threshold: a candidate must clear ``match_threshold`` and carry an
        # authorized runbook action. Non-gold reference memories (distractors
        # and hard negatives) carry no actions, so they can pollute the ranked
        # list -- which is exactly what depresses recall@1/nDCG -- without ever
        # being authorized. There is no oracle-aware special case here.
        candidate = next(
            (
                item
                for item in top
                if item[0] >= self.match_threshold and item[2].actions
            ),
            None,
        )

        if candidate is None:
            return ResponderDecision(
                actions=(
                    ActionPlan(
                        action_type="no_op_page_human",
                        target_service=observation.service,
                        params={},
                    ),
                ),
                retrieved=hits,
                token_proxy=base_tokens + 80,
                abstained=True,
                decision_seconds=DECISION_LATENCY_NOT_MEASURED,
            )

        rendered = tuple(
            self._render_action(action, observation)
            for action in candidate[2].actions
        )
        return ResponderDecision(
            actions=rendered,
            retrieved=hits,
            token_proxy=base_tokens + 70 * len(rendered),
            authorized_memory_id=candidate[1],
            decision_seconds=DECISION_LATENCY_NOT_MEASURED,
        )

    @staticmethod
    def _similarity(
        query_tokens: frozenset[str], memory_tokens: frozenset[str]
    ) -> float:
        if not query_tokens or not memory_tokens:
            return 0.0
        return len(query_tokens & memory_tokens) / math.sqrt(
            len(query_tokens) * len(memory_tokens)
        )

    @staticmethod
    def _render_action(
        action: ActionPlan, observation: IncidentObservation
    ) -> ActionPlan:
        target = (
            observation.service
            if action.target_service == "$incident.service"
            else action.target_service
        )
        params: dict[str, Any] = {}
        for key, value in action.params.items():
            if value == "$incident.previous_stable_version":
                value = observation.previous_stable_version
            params[key] = value
        return ActionPlan(action.action_type, target, params)


class MemorylessBaselineResponder:
    """Competent memoryless baseline (Reality Charter R2).

    A fair comparison arm: it has *no* persistent incident memory, but it is
    not handicapped. It reads the current incident signal and applies the best
    honest first-line remediation a competent on-call engineer would reach for
    from generic runbook knowledge -- and it correctly *abstains* (pages a
    human) when the signal is ambiguous or unrecognized rather than guessing.
    It never plays a deliberately-wrong action to flatter the memory arm.

    Because the deterministic simulator's oracle only resolves an incident when
    the exact tuned remediation is applied, a competent baseline that reads the
    signal well resolves the same incidents the memory arm does. That is the
    honest finding of a deterministic harness: it cannot, on its own,
    demonstrate that memory improves decision quality. Any such claim (MTTR
    delta, first-action accuracy, wrong-action rate) is deferred to the real
    Bedrock agent run per R7 and is reported as ``pending_real_agent_run``.
    """

    name = "competent_baseline"

    def decide(self, observation: IncidentObservation) -> ResponderDecision:
        signal = observation.signal.lower()
        target = observation.service
        actions = self._first_line_remediation(signal, target, observation)
        query_tokens = len(tokenize(f"{observation.title} {observation.signal}"))
        return ResponderDecision(
            actions=actions,
            token_proxy=280 + query_tokens * 4 + len(actions) * 70,
            abstained=actions[-1].action_type == "no_op_page_human",
            decision_seconds=DECISION_LATENCY_NOT_MEASURED,
        )

    @staticmethod
    def _first_line_remediation(
        signal: str, target: str, observation: IncidentObservation
    ) -> tuple[ActionPlan, ...]:
        if "after deploy" in signal:
            return (
                ActionPlan(
                    "rollback_deploy",
                    target,
                    {"target_version": observation.previous_stable_version},
                ),
            )
        if "too many connections" in signal:
            return (
                ActionPlan(
                    "set_config", target, {"key": "db_pool_size", "value": 80}
                ),
                ActionPlan("restart_service", target, {}),
            )
        if "cache timeout cascade" in signal:
            return (
                ActionPlan(
                    "failover_dependency",
                    target,
                    {
                        "dependency_key": "checkout-cache",
                        "to_service": "redis-cluster-v2-replica",
                    },
                ),
            )
        if "traffic spike saturated" in signal:
            return (
                ActionPlan("scale_service", target, {"capacity_units": 8}),
                ActionPlan("throttle_traffic", target, {"traffic_percent": 75}),
            )
        if "retry storm" in signal:
            return (
                ActionPlan("throttle_traffic", target, {"traffic_percent": 60}),
            )
        if "memory leak" in signal:
            return (ActionPlan("restart_service", target, {}),)
        if "config flag" in signal:
            return (
                ActionPlan(
                    "set_config",
                    target,
                    {"key": "express_checkout_enabled", "value": False},
                ),
            )
        if "datastore primary" in signal:
            return (
                ActionPlan(
                    "failover_dependency",
                    target,
                    {
                        "dependency_key": "checkout-orders",
                        "to_service": "orders-db-replica",
                    },
                ),
            )
        if "payment provider" in signal:
            return (
                ActionPlan(
                    "failover_dependency",
                    target,
                    {
                        "dependency_key": "payment-provider",
                        "to_service": "payment-provider-secondary",
                    },
                ),
                ActionPlan("throttle_traffic", target, {"traffic_percent": 50}),
            )
        # Ambiguous / unrecognized signal (novel families, and near-misses such
        # as a slow-query plan that merely resembles pool pressure): a competent
        # engineer does not guess -- they page a human.
        return (ActionPlan("no_op_page_human", target, {}),)
