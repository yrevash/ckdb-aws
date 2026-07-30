from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
from typing import Any

from postmortem_sim import Conductor

from .contracts import Responder
from .responders import (
    DEFAULT_MATCH_THRESHOLD,
    ColdStartResponder,
    ProceduralMemoryResponder,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
SCHEMA_VERSION = "postmortem-eval-v1"
TOKEN_COST_PROXY_USD = 0.000003


@dataclass(frozen=True)
class IncidentResult:
    scenario_id: str
    incident_id: str
    family_id: str
    variant_id: str
    occurrence: int
    mttr_seconds: int
    actions: int
    wrong_actions: int
    first_action_correct: bool
    failed_orders: int
    failed_order_value_cents: int
    escalated: bool
    abstained: bool
    token_proxy: int
    retrieved_memory_ids: tuple[str, ...]
    top_retrieval_score: float | None
    authorized_memory_id: str | None


class EvaluationHarness:
    """Controlled A/B replay over identical deterministic incident streams."""

    def __init__(
        self,
        *,
        scenario_path: Path | str | None = None,
        oracle_path: Path | str | None = None,
        corpus_path: Path | str = FIXTURE_ROOT / "memory_corpus.json",
        retrieval_k: int = 10,
    ) -> None:
        self.scenario_path = scenario_path
        self.oracle_path = oracle_path
        self.corpus_path = Path(corpus_path)
        self.retrieval_k = retrieval_k
        corpus = json.loads(self.corpus_path.read_text())
        self.gold_by_family: dict[str, set[str]] = {}
        for item in corpus["memories"]:
            if item.get("gold", False):
                self.gold_by_family.setdefault(item["family_id"], set()).add(
                    item["memory_id"]
                )

    def run(self) -> dict[str, Any]:
        memory_results, memory_seed = self._run_arm(
            ProceduralMemoryResponder(
                self.corpus_path,
                retrieval_k=self.retrieval_k,
            )
        )
        cold_results, cold_seed = self._run_arm(ColdStartResponder())
        if memory_seed != cold_seed:
            raise AssertionError("A/B arms did not use the same simulator seed")
        if [item.scenario_id for item in memory_results] != [
            item.scenario_id for item in cold_results
        ]:
            raise AssertionError("A/B arms did not replay the same scenario stream")
        if [item.incident_id for item in memory_results] != [
            item.incident_id for item in cold_results
        ]:
            raise AssertionError("A/B arms produced different deterministic IDs")

        memory_summary = self._summarize(memory_results)
        cold_summary = self._summarize(cold_results)
        recall = self._recall_metrics(memory_results)
        comparison = {
            "median_mttr_reduction_percent": self._reduction(
                cold_summary["median_mttr_seconds"],
                memory_summary["median_mttr_seconds"],
            ),
            "failed_orders_avoided": (
                cold_summary["failed_orders"] - memory_summary["failed_orders"]
            ),
            "failed_order_value_avoided_cents": (
                cold_summary["failed_order_value_cents"]
                - memory_summary["failed_order_value_cents"]
            ),
            "wrong_actions_avoided": (
                cold_summary["wrong_actions"] - memory_summary["wrong_actions"]
            ),
            "token_proxy_reduction_percent": self._reduction(
                cold_summary["token_proxy_total"],
                memory_summary["token_proxy_total"],
            ),
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": "2026-07-30T00:00:00Z",
            "seed": memory_seed,
            "method": {
                "controlled_variable": "persistent procedural memory enabled",
                "scenario_stream_identical": True,
                "simulated_clock": True,
                "token_cost_proxy_usd_per_token": TOKEN_COST_PROXY_USD,
                "retrieval_k": self.retrieval_k,
                "abstention_match_threshold": DEFAULT_MATCH_THRESHOLD,
                "decision_time_model": {
                    "with_memory_seconds_by_use": [180, 60, 0],
                    "cold_start_seconds": 240,
                },
            },
            "recall": recall,
            "learning_curve": {
                "with_memory": self._learning_curve(memory_results),
                "cold_start": self._learning_curve(cold_results),
            },
            "arms": {
                "with_memory": {
                    "summary": memory_summary,
                    "incidents": [
                        self._incident_dict(item) for item in memory_results
                    ],
                },
                "cold_start": {
                    "summary": cold_summary,
                    "incidents": [
                        self._incident_dict(item) for item in cold_results
                    ],
                },
            },
            "comparison": comparison,
        }

    def write_json(self, output_path: Path | str) -> dict[str, Any]:
        report = self.run()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    def _run_arm(
        self, responder: Responder
    ) -> tuple[list[IncidentResult], int]:
        conductor = self._new_conductor()
        results: list[IncidentResult] = []
        occurrences: dict[str, int] = {}
        while conductor.remaining_scenarios:
            initial_order_index = len(conductor.state.orders)
            initial_action_index = len(conductor.state.actions)
            incident = conductor.inject_next()
            occurrences[incident.family_id] = (
                occurrences.get(incident.family_id, 0) + 1
            )
            decision = responder.decide(
                conductor.observe_incident(incident.incident_id)
            )
            conductor.advance_time(decision.decision_seconds)

            action_outcomes: list[str] = []
            for action in decision.actions:
                result = conductor.apply_action(
                    incident.incident_id,
                    action.action_type,
                    action.target_service,
                    action.params,
                    memory_ref=(
                        f"memory:{decision.authorized_memory_id}"
                        if decision.authorized_memory_id
                        else None
                    ),
                )
                action_outcomes.append(result.outcome)
                if result.resolved:
                    break

            if incident.mttr_seconds is None:
                raise AssertionError(
                    f"{responder.name} failed to resolve {incident.scenario_id}"
                )

            orders = [
                order
                for order in conductor.state.orders[initial_order_index:]
                if order.incident_id == incident.incident_id
            ]
            actions = [
                action
                for action in conductor.state.actions[initial_action_index:]
                if action.incident_id == incident.incident_id
            ]
            results.append(
                IncidentResult(
                    scenario_id=incident.scenario_id,
                    incident_id=incident.incident_id,
                    family_id=incident.family_id,
                    variant_id=incident.variant_id,
                    occurrence=occurrences[incident.family_id],
                    mttr_seconds=incident.mttr_seconds,
                    actions=len(actions),
                    wrong_actions=sum(
                        outcome == "no_effect" for outcome in action_outcomes
                    ),
                    first_action_correct=bool(action_outcomes)
                    and action_outcomes[0] == "success",
                    failed_orders=len(orders),
                    failed_order_value_cents=sum(
                        order.amount_cents for order in orders
                    ),
                    escalated=any(
                        action.action_type
                        in {"no_op_page_human", "escalate"}
                        for action in actions
                    ),
                    abstained=decision.abstained,
                    token_proxy=decision.token_proxy,
                    retrieved_memory_ids=tuple(
                        hit.memory_id for hit in decision.retrieved
                    ),
                    top_retrieval_score=(
                        decision.retrieved[0].score
                        if decision.retrieved
                        else None
                    ),
                    authorized_memory_id=decision.authorized_memory_id,
                )
            )
        return results, conductor.seed

    def _new_conductor(self) -> Conductor:
        kwargs: dict[str, Any] = {}
        if self.scenario_path is not None:
            kwargs["scenario_path"] = self.scenario_path
        if self.oracle_path is not None:
            kwargs["oracle_path"] = self.oracle_path
        return Conductor.from_files(**kwargs)

    def _summarize(self, results: list[IncidentResult]) -> dict[str, Any]:
        mttr = [item.mttr_seconds for item in results]
        tokens = sum(item.token_proxy for item in results)
        return {
            "incidents": len(results),
            "resolved": len(results),
            "median_mttr_seconds": statistics.median(mttr),
            "p90_mttr_seconds": self._percentile(mttr, 0.9),
            "mean_actions_to_resolution": round(
                statistics.mean(item.actions for item in results), 3
            ),
            "first_action_accuracy": round(
                statistics.mean(item.first_action_correct for item in results),
                6,
            ),
            "wrong_actions": sum(item.wrong_actions for item in results),
            "escalations": sum(item.escalated for item in results),
            "failed_orders": sum(item.failed_orders for item in results),
            "failed_order_value_cents": sum(
                item.failed_order_value_cents for item in results
            ),
            "token_proxy_total": tokens,
            "token_proxy_mean": round(tokens / len(results), 3),
            "cost_proxy_usd": round(tokens * TOKEN_COST_PROXY_USD, 6),
        }

    def _recall_metrics(
        self, results: list[IncidentResult]
    ) -> dict[str, Any]:
        scored = [
            item
            for item in results
            if item.family_id in self.gold_by_family
            and item.variant_id != "red-herring-slow-query"
        ]
        metrics: dict[str, Any] = {"queries": len(scored)}
        for k in (1, 5, 10):
            hits = sum(
                bool(
                    set(item.retrieved_memory_ids[:k])
                    & self.gold_by_family[item.family_id]
                )
                for item in scored
            )
            metrics[f"recall_at_{k}"] = round(hits / len(scored), 6)

        precision_values: list[float] = []
        ndcg_values: list[float] = []
        for item in scored:
            retrieved = item.retrieved_memory_ids[:10]
            gold = self.gold_by_family[item.family_id]
            precision_values.append(len(set(retrieved) & gold) / 10)
            dcg = sum(
                1 / math.log2(rank + 2)
                for rank, memory_id in enumerate(retrieved)
                if memory_id in gold
            )
            ideal_hits = min(len(gold), 10)
            ideal_dcg = sum(
                1 / math.log2(rank + 2) for rank in range(ideal_hits)
            )
            ndcg_values.append(dcg / ideal_dcg if ideal_dcg else 0.0)
        metrics["precision_at_10"] = round(
            statistics.mean(precision_values), 6
        )
        metrics["ndcg_at_10"] = round(statistics.mean(ndcg_values), 6)

        novel = [item for item in results if item.family_id == "F10_NOVEL"]
        metrics["abstention_accuracy"] = round(
            statistics.mean(item.abstained for item in novel), 6
        )
        near_miss = [
            item
            for item in results
            if item.variant_id == "red-herring-slow-query"
        ]
        metrics["near_miss_queries"] = len(near_miss)
        metrics["near_miss_safe_rejection_accuracy"] = round(
            statistics.mean(
                item.abstained
                and item.authorized_memory_id != "mem-f2-pool"
                for item in near_miss
            ),
            6,
        )
        metrics["pool_runbook_near_miss_authorization_rate"] = round(
            statistics.mean(
                item.authorized_memory_id == "mem-f2-pool"
                for item in near_miss
            ),
            6,
        )
        return metrics

    def _learning_curve(
        self, results: list[IncidentResult]
    ) -> list[dict[str, Any]]:
        curve: list[dict[str, Any]] = []
        occurrences = sorted(
            {
                item.occurrence
                for item in results
                if item.family_id != "F10_NOVEL"
            }
        )
        for occurrence in occurrences:
            group = [
                item
                for item in results
                if item.family_id != "F10_NOVEL"
                and item.occurrence == occurrence
            ]
            curve.append(
                {
                    "occurrence": occurrence,
                    "incidents": len(group),
                    "median_mttr_seconds": statistics.median(
                        item.mttr_seconds for item in group
                    ),
                    "p90_mttr_seconds": self._percentile(
                        [item.mttr_seconds for item in group],
                        0.9,
                    ),
                    "first_action_accuracy": round(
                        statistics.mean(
                            item.first_action_correct for item in group
                        ),
                        6,
                    ),
                    "mean_actions_to_resolution": round(
                        statistics.mean(item.actions for item in group),
                        6,
                    ),
                }
            )
        return curve

    @staticmethod
    def _percentile(values: list[int], quantile: float) -> int:
        ordered = sorted(values)
        index = max(0, math.ceil(quantile * len(ordered)) - 1)
        return ordered[index]

    @staticmethod
    def _reduction(baseline: float, improved: float) -> float:
        if baseline == 0:
            return 0.0
        return round((baseline - improved) / baseline * 100, 3)

    @staticmethod
    def _incident_dict(result: IncidentResult) -> dict[str, Any]:
        payload = asdict(result)
        payload["retrieved_memory_ids"] = list(result.retrieved_memory_ids)
        return payload
