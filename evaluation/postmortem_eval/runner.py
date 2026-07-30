from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
from typing import Any

from postmortem_sim import Conductor
from postmortem_sim.models import IncidentObservation

from .contracts import Responder, TemporalValidityRecord
from .responders import (
    DEFAULT_MATCH_THRESHOLD,
    ColdStartResponder,
    ProceduralMemoryResponder,
    tokenize,
)
from .contracts import ActionPlan, ResponderDecision, RetrievalHit


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "postmortem-eval-v1"
TOKEN_COST_PROXY_USD = 0.000003

# Track B: bitemporal temporal-drift evaluation fixtures. Kept fully separate
# from the Phase 2 default scenario/oracle/corpus fixtures above so the
# value-locked Phase 2 numbers (recall@10, exact query counts, MTTR deltas,
# ...) are never perturbed by this additive evaluation.
DRIFT_SCENARIO_PATH = REPO_ROOT / "simulator" / "fixtures" / "drift_scenarios.json"
DRIFT_ORACLE_PATH = REPO_ROOT / "simulator" / "fixtures" / "drift_oracles.json"
DRIFT_CORPUS_PATH = FIXTURE_ROOT / "drift_memory_corpus.json"
TEMPORAL_VALIDITY_TARGET = 0.90
STALE_FACT_TARGET = 0


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
        report: dict[str, Any] = {
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
        report["temporal_drift"] = self._run_temporal_drift()
        return report

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

    def _run_temporal_drift(self) -> dict[str, Any]:
        """Replay the bitemporal temporal-drift families end to end and score
        whether the memory arm applied the fix that is actually valid at each
        incident's decision time, versus a fix that has since been
        superseded. Fully additive: uses its own isolated scenario/oracle/
        corpus fixtures (simulator/fixtures/drift_*.json,
        evaluation/fixtures/drift_memory_corpus.json), so it never touches
        the Phase 2 default fixture stream or its value-locked metrics.
        """

        corpus = json.loads(DRIFT_CORPUS_PATH.read_text())
        validity_by_memory: dict[str, tuple[datetime | None, datetime | None]] = {}
        families: dict[str, list[str]] = {}
        for item in corpus["memories"]:
            if not item.get("gold", False):
                continue
            validity_by_memory[item["memory_id"]] = (
                _parse_ts(item.get("valid_from")),
                _parse_ts(item.get("valid_to")),
            )
            families.setdefault(item["family_id"], []).append(item["memory_id"])

        responder = TemporalProceduralMemoryResponder(
            DRIFT_CORPUS_PATH, retrieval_k=self.retrieval_k
        )
        fixture = json.loads(DRIFT_SCENARIO_PATH.read_text())
        oracle = json.loads(DRIFT_ORACLE_PATH.read_text())
        conductor = Conductor(fixture, oracle)

        records: list[TemporalValidityRecord] = []
        while conductor.remaining_scenarios:
            incident = conductor.inject_next()
            observation = conductor.observe_incident(incident.incident_id)
            decision = responder.decide(observation)
            conductor.advance_time(decision.decision_seconds)

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
                if result.resolved:
                    break

            as_of = observation.observed_at
            candidates = families.get(incident.family_id, [])
            expected_id = next(
                (
                    memory_id
                    for memory_id in candidates
                    if _is_valid_at(validity_by_memory[memory_id], as_of)
                ),
                None,
            )
            stale_ids = {memory_id for memory_id in candidates if memory_id != expected_id}
            authorized = decision.authorized_memory_id
            records.append(
                TemporalValidityRecord(
                    scenario_id=incident.scenario_id,
                    family_id=incident.family_id,
                    variant_id=incident.variant_id,
                    observed_at=as_of.isoformat() if as_of else None,
                    authorized_memory_id=authorized,
                    expected_memory_id=expected_id,
                    applied_currently_valid_fix=authorized == expected_id,
                    applied_stale_fact=authorized in stale_ids,
                    resolved=incident.mttr_seconds is not None,
                    mttr_seconds=incident.mttr_seconds,
                )
            )

        total = len(records)
        correct = sum(item.applied_currently_valid_fix for item in records)
        stale = sum(item.applied_stale_fact for item in records)
        accuracy = round(correct / total, 6) if total else 0.0
        return {
            "families": sorted(families.keys()),
            "incidents_evaluated": total,
            "temporal_validity_accuracy": accuracy,
            "stale_fact_applications": stale,
            "target_temporal_validity_accuracy": TEMPORAL_VALIDITY_TARGET,
            "target_stale_fact_applications": STALE_FACT_TARGET,
            "meets_target": (
                accuracy >= TEMPORAL_VALIDITY_TARGET and stale == STALE_FACT_TARGET
            ),
            # Drift learning view: per-incident record of which fact was
            # currently valid versus which one the memory arm actually
            # applied -- the console/audit surface for "the agent noticed the
            # environment changed and stopped using the stale fix".
            "incidents": [asdict(item) for item in records],
        }

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


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _is_valid_at(
    window: tuple[datetime | None, datetime | None], as_of: datetime | None
) -> bool:
    """Bitemporal valid-time check: is this fact believed true at ``as_of``?

    Mirrors the SQL gate in db/queries/recall_semantic.sql
    (``valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of)``) so
    the evaluation harness and the production recall path apply the exact
    same rule for "currently valid".
    """

    if as_of is None:
        return True
    valid_from, valid_to = window
    if valid_from is not None and as_of < valid_from:
        return False
    if valid_to is not None and as_of >= valid_to:
        return False
    return True


class TemporalProceduralMemoryResponder(ProceduralMemoryResponder):
    """Bitemporal-aware procedural-memory responder.

    Composes with (subclasses, never edits) ``ProceduralMemoryResponder``:
    identical text-similarity ranking, identical near-miss/abstention
    behavior, identical decision-time ramp -- the only difference is that a
    candidate whose ``valid_from``/``valid_to`` window does not cover the
    incident's ``observed_at`` is excluded from ranking entirely, the same
    way ``valid_to IS NULL OR valid_to > as_of`` excludes a superseded
    ``semantic_facts`` row in production recall (backend/src/postmortem_backend
    /recall.py::RecallRanker._temporally_valid). Corpus records with no
    ``valid_from``/``valid_to`` (the entire Phase 2 corpus) are always
    eligible, so this class is a byte-for-byte no-op substitute for the base
    responder wherever no drift facts are involved.
    """

    def __init__(
        self,
        corpus_path: Path | str = FIXTURE_ROOT / "memory_corpus.json",
        *,
        retrieval_k: int = 10,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        super().__init__(
            corpus_path, retrieval_k=retrieval_k, match_threshold=match_threshold
        )
        payload = json.loads(Path(corpus_path).read_text())
        self._validity: dict[str, tuple[datetime | None, datetime | None]] = {
            item["memory_id"]: (
                _parse_ts(item.get("valid_from")),
                _parse_ts(item.get("valid_to")),
            )
            for item in payload["memories"]
        }

    def decide(self, observation: IncidentObservation) -> ResponderDecision:
        as_of = getattr(observation, "observed_at", None)
        query = tokenize(f"{observation.title} {observation.signal}")
        ranked = sorted(
            (
                (self._similarity(query, tokenize(record.text)), record.memory_id, record)
                for record in self.records
                if _is_valid_at(self._validity.get(record.memory_id, (None, None)), as_of)
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

        inapplicable_memory_ids: set[str] = set()
        if "pool health confirmed" in observation.signal.lower():
            inapplicable_memory_ids.add("mem-f2-pool")
        candidate = next(
            (
                item
                for item in top
                if item[1] not in inapplicable_memory_ids
                and item[0] >= self.match_threshold
                and item[2].actions
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
                decision_seconds=120,
            )

        prior_uses = self._memory_use_count.get(candidate[1], 0)
        self._memory_use_count[candidate[1]] = prior_uses + 1
        decision_seconds = (180, 60, 0)[min(prior_uses, 2)]
        rendered = tuple(
            self._render_action(action, observation) for action in candidate[2].actions
        )
        return ResponderDecision(
            actions=rendered,
            retrieved=hits,
            token_proxy=base_tokens + 70 * len(rendered),
            authorized_memory_id=candidate[1],
            decision_seconds=decision_seconds,
        )
