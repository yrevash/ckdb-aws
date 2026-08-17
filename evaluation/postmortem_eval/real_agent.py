"""Real-agent decision quality: does retrieved memory make the agent better?

This is the one measurement the deterministic harness cannot make (Reality
Charter R7). ``runner.py`` reports ``decision_quality`` as
``pending_real_agent_run`` precisely because a scripted responder answering a
scripted world tells you nothing about *reasoning*. This module replaces the
script with the real Bedrock model on both sides of the comparison.

**The controlled variable is exactly one thing: retrieved memory in context.**

Both arms use the same model, the same system prompt, the same action schema,
the same service/dependency catalog, and the same incident stream in the same
order. The ``with_memory`` arm additionally receives the top-k memories from
the *same* text ranker the ``retrieval`` section already scores -- hard
negatives and all. So the claim this module supports is precisely:

    "Given retrieval measured at recall@1 = <the reported figure>, does putting
     those retrieved memories in the agent's context improve its decisions?"

Not "is our vector search good" (that is the ``retrieval`` section) and not
"is Claude good" (both arms are Claude). Just the memory.

Fairness notes (Reality Charter R2):

* The baseline is **not** handicapped. It gets the same model, the same
  reasoning budget, the same catalog of services and dependencies a real
  on-call engineer would have open, and the same freedom to abstain. It is
  missing only the institutional memory of prior incidents.
* Neither arm ever sees ``family_id``, the oracle, or the required action.
  ``IncidentObservation`` excludes them by construction and nothing here
  reintroduces them.
* Abstention is a first-class correct answer, not a forfeit: the F10_NOVEL
  family's oracle *requires* ``no_op_page_human``. An arm that pages a human on
  a genuinely novel incident resolves it; an arm that guesses does not.

Unresolved incidents are recorded rather than raised. A real agent that fails
to fix an incident is data, not a harness bug -- which is why this module does
not reuse ``EvaluationHarness._run_arm`` (that one asserts resolution).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any

from postmortem_sim import Conductor
from postmortem_sim.conductor import SimulationError
from postmortem_sim.models import IncidentObservation

from .contracts import ActionPlan, ResponderDecision, RetrievalHit
from .responders import FIXTURE_ROOT, ProceduralMemoryResponder, tokenize


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "postmortem-decision-quality-v1"

PRODUCED_BY = (
    "python -m postmortem_eval.real_agent "
    "(evaluation/postmortem_eval/real_agent.py)"
)

# Matches the backend's production reasoning model so this measurement
# describes the agent that actually ships (backend/src/postmortem_backend/
# config.py, infra/postmortem_infra/stacks.py).
DEFAULT_MODEL_ID = os.getenv(
    "POSTMORTEM_REASONING_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
DEFAULT_REGION = os.getenv("AWS_REGION") or os.getenv(
    "POSTMORTEM_BEDROCK_REGION", "us-east-1"
)

# Bounded like the production adapter (audit backend#8): a stalled endpoint
# must not hang the run. Retries cover transient throttling, which is likely
# when replaying 29 incidents twice back to back.
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 60
_MAX_ATTEMPTS = 5


# --- the action contract shown to BOTH arms -------------------------------
#
# This is the tool surface, not the answer key. It lists which actions exist
# and what parameters each one takes -- exactly what an operator's runbook
# tooling would document. It deliberately contains no mapping from symptom to
# action, no parameter *values*, and no mention of incident families.

ACTION_SCHEMA = """\
Available remediation actions. Each action is an object:
  {"action_type": <one of below>, "target_service": <service name>, "params": {...}}

  rollback_deploy      params: {"target_version": "<version string>"}
  set_config           params: {"key": "<config key>", "value": <any>}
  restart_service      params: {}
  failover_dependency  params: {"dependency_key": "<key>", "to_service": "<service name>"}
  scale_service        params: {"capacity_units": <positive integer>}
  throttle_traffic     params: {"traffic_percent": <integer 1-100>}
  no_op_page_human     params: {}    -- escalate to a human; use when the
                                        signal is unrecognized or ambiguous

Actions are applied in the order you list them and stop as soon as the
incident resolves. A wrong action costs significantly more time than a right
one, and every action taken while the incident is open loses customer orders.
"""

_SYSTEM_PROMPT = """\
You are Postmortem, an autonomous on-call SRE responder for a payments
platform. You are handed one open incident and must decide what to do.

Judgment:
- Prefer the smallest plan that actually resolves the incident.
- Guessing is expensive. If the signal does not clearly indicate a specific
  remediation, escalate with no_op_page_human rather than trying something
  plausible. Escalating on a genuinely unfamiliar incident is the correct
  answer, not a failure.

Return exactly one JSON object and no other text:
{
  "reasoning": "<one or two sentences>",
  "actions": [ {"action_type": "...", "target_service": "...", "params": {...}} ],
  "cited_memory_id": "<memory_id you based this on, or null>"
}
"""

_MEMORY_RULES = """\
You have been given memories of prior incidents retrieved from the team's
incident database. They are ranked by similarity to the current incident, and
the ranking is imperfect: some retrieved memories describe incidents that
merely LOOK like this one and had a different cause. Judge each on whether its
description actually matches the signal in front of you.

If you act on a memory's runbook, set "cited_memory_id" to that memory's id.
If none of them genuinely fits, ignore them all, set "cited_memory_id" to null,
and decide from the incident signal alone.
"""

_NO_MEMORY_RULES = """\
You have no record of prior incidents on this platform. Decide from the
incident signal and the service catalog alone. Set "cited_memory_id" to null.
"""


class ReasoningFailure(RuntimeError):
    """The model did not return a usable plan after retries."""


def _bedrock_client(region: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=_CONNECT_TIMEOUT,
            read_timeout=_READ_TIMEOUT,
            retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
        ),
    )


def _catalog(conductor: Conductor) -> str:
    """The service/dependency inventory, identical for both arms.

    This is environment state an on-call engineer reads off a service
    catalogue, not privileged knowledge: which services exist, their tier, and
    how they depend on each other. Withholding it from the memoryless arm
    would handicap it (R2). It carries no symptom-to-action mapping.
    """

    services = [
        {
            "name": service.name,
            "tier": service.tier,
            "health": str(service.health),
            "current_version": service.current_version,
            "previous_stable_version": service.previous_stable_version,
            "capacity_units": service.capacity_units,
        }
        for service in sorted(
            conductor.state.services.values(), key=lambda item: item.name
        )
    ]
    dependencies = [
        {
            "dependency_key": dependency.dependency_key,
            "from_service": dependency.from_service,
            "currently_pointing_at": dependency.to_service,
            "criticality": dependency.criticality,
        }
        for dependency in sorted(
            conductor.state.dependencies.values(),
            key=lambda item: item.dependency_key,
        )
    ]
    return json.dumps(
        {"services": services, "dependencies": dependencies}, indent=2
    )


def _incident_block(observation: IncidentObservation) -> str:
    return json.dumps(
        {
            "title": observation.title,
            "service": observation.service,
            "severity": observation.severity,
            "alert_signal": observation.signal,
            "slo_kind": observation.slo_kind,
            "slo_current_value": observation.slo_current_value,
            "slo_threshold": observation.slo_threshold,
            "current_version": observation.current_version,
            "previous_stable_version": observation.previous_stable_version,
            "observed_at": observation.observed_at.isoformat(),
        },
        indent=2,
    )


def _parse_plan(raw: str) -> tuple[list[dict[str, Any]], str | None, str]:
    """Extract the plan from the model's text. Untrusted input."""

    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        payload = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError) as exc:
        raise ReasoningFailure("model did not return a JSON object") from exc

    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ReasoningFailure("model returned no actions")

    cited = payload.get("cited_memory_id")
    cited_id = str(cited) if cited else None
    reasoning = str(payload.get("reasoning", "")).strip()
    return actions, cited_id, reasoning


class BedrockIncidentResponder(ProceduralMemoryResponder):
    """One arm of the real-agent comparison.

    Subclasses ``ProceduralMemoryResponder`` purely to inherit its retrieval
    ranking and action templating -- the memory arm must retrieve exactly the
    way the scored ``retrieval`` section retrieves, or the MTTR number would
    describe a retriever nobody measured. ``decide`` is fully replaced: no
    similarity threshold, no scripted authorization. The model decides.
    """

    def __init__(
        self,
        *,
        arm: str,
        client: Any,
        model_id: str,
        catalog: str,
        corpus_path: Path | str = FIXTURE_ROOT / "memory_corpus.json",
        retrieval_k: int = 5,
    ) -> None:
        super().__init__(corpus_path, retrieval_k=retrieval_k)
        if arm not in {"with_memory", "no_memory"}:
            raise ValueError(f"unknown arm: {arm}")
        self.name = arm
        self.uses_memory = arm == "with_memory"
        self._client = client
        self._model_id = model_id
        self._catalog = catalog

    # -- retrieval (memory arm only) -----------------------------------

    def _retrieve(
        self, observation: IncidentObservation
    ) -> list[tuple[float, str, Any]]:
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
        return ranked[: self.retrieval_k]

    def _memory_block(
        self,
        top: list[tuple[float, str, Any]],
        observation: IncidentObservation,
    ) -> str:
        items = []
        for score, memory_id, record in top:
            items.append(
                {
                    "memory_id": memory_id,
                    "similarity": round(score, 4),
                    "description": record.text,
                    # Rendered against this incident so the model sees a
                    # concrete plan, the same way production recall resolves
                    # templated runbook steps before handing them to the agent.
                    "runbook_actions": [
                        asdict(self._render_action(action, observation))
                        for action in record.actions
                    ],
                }
            )
        return json.dumps(items, indent=2)

    # -- the model call -------------------------------------------------

    def decide(self, observation: IncidentObservation) -> ResponderDecision:
        top = self._retrieve(observation) if self.uses_memory else []
        hits = tuple(
            RetrievalHit(memory_id=memory_id, score=round(score, 6))
            for score, memory_id, _ in top
        )

        sections = [
            "## Open incident",
            _incident_block(observation),
            "",
            "## Service catalog",
            self._catalog,
            "",
            "## Remediation actions",
            ACTION_SCHEMA,
            "",
        ]
        if self.uses_memory:
            sections += [
                "## Retrieved prior incidents",
                _MEMORY_RULES,
                self._memory_block(top, observation),
            ]
        else:
            sections += ["## Prior incidents", _NO_MEMORY_RULES]

        started = time.monotonic()
        response = self._client.converse(
            modelId=self._model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": "\n".join(sections)}]}],
            inferenceConfig={"maxTokens": 1200, "temperature": 0.0},
        )
        elapsed = time.monotonic() - started

        blocks = response["output"]["message"]["content"]
        raw = "".join(block.get("text", "") for block in blocks)
        usage = response.get("usage", {})

        try:
            raw_actions, cited_id, _ = _parse_plan(raw)
        except ReasoningFailure:
            # An unparseable plan is a real failure of the agent, recorded as
            # such: it escalates by default rather than crashing the run.
            return ResponderDecision(
                actions=(
                    ActionPlan("no_op_page_human", observation.service, {}),
                ),
                retrieved=hits,
                abstained=True,
                decision_seconds=elapsed,
                input_tokens=int(usage.get("inputTokens", 0)),
                output_tokens=int(usage.get("outputTokens", 0)),
                model_id=self._model_id,
                invalid_plan=True,
            )

        # Provenance guard, mirroring the backend's recall gate: a citation
        # that was never retrieved is not evidence. Drop it rather than let a
        # hallucinated id count as memory-grounded.
        known = {memory_id for _, memory_id, _ in top}
        authorized = cited_id if cited_id in known else None

        actions = tuple(
            ActionPlan(
                action_type=str(item.get("action_type", "")),
                target_service=str(
                    item.get("target_service") or observation.service
                ),
                params=dict(item.get("params") or {}),
            )
            for item in raw_actions
            if isinstance(item, dict)
        )
        if not actions:
            actions = (ActionPlan("no_op_page_human", observation.service, {}),)

        return ResponderDecision(
            actions=actions,
            retrieved=hits,
            abstained=actions[0].action_type in {"no_op_page_human", "escalate"},
            authorized_memory_id=authorized,
            decision_seconds=elapsed,
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            model_id=self._model_id,
        )


@dataclass(frozen=True)
class RealIncidentResult:
    scenario_id: str
    incident_id: str
    family_id: str
    variant_id: str
    resolved: bool
    mttr_seconds: int | None
    decision_seconds: float
    actions_applied: int
    wrong_actions: int
    invalid_actions: int
    first_action_correct: bool
    abstained: bool
    escalated: bool
    failed_orders: int
    failed_order_value_cents: int
    input_tokens: int
    output_tokens: int
    retrieved_memory_ids: tuple[str, ...] = ()
    authorized_memory_id: str | None = None
    invalid_plan: bool = False


def _run_arm(
    responder: BedrockIncidentResponder,
    *,
    scenario_path: Path | str | None = None,
    oracle_path: Path | str | None = None,
    conductor: Conductor | None = None,
) -> list[RealIncidentResult]:
    """Replay the full incident stream through one arm.

    Unlike ``EvaluationHarness._run_arm`` this never asserts resolution: an
    incident the agent could not fix is recorded with ``resolved=False`` and a
    null MTTR, because that is the outcome being measured.
    """

    if conductor is None:
        kwargs: dict[str, Any] = {}
        if scenario_path is not None:
            kwargs["scenario_path"] = scenario_path
        if oracle_path is not None:
            kwargs["oracle_path"] = oracle_path
        conductor = Conductor.from_files(**kwargs)

    results: list[RealIncidentResult] = []
    while conductor.remaining_scenarios:
        order_mark = len(conductor.state.orders)
        incident = conductor.inject_next()
        observation = conductor.observe_incident(incident.incident_id)
        decision = responder.decide(observation)

        # Thinking time is real time. The incident stays open while the agent
        # reasons, so it belongs in MTTR -- and is also reported separately so
        # the reader can subtract it.
        conductor.advance_time(decision.decision_seconds)

        outcomes: list[str] = []
        invalid = 0
        applied = 0
        for action in decision.actions:
            try:
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
            except SimulationError:
                # The model produced a step the platform cannot execute
                # (unknown action, missing/invalid parameter). No world state
                # changes and no time passes; it counts against the agent.
                invalid += 1
                continue
            applied += 1
            outcomes.append(result.outcome)
            if result.resolved:
                break

        orders = [
            order
            for order in conductor.state.orders[order_mark:]
            if order.incident_id == incident.incident_id
        ]
        results.append(
            RealIncidentResult(
                scenario_id=incident.scenario_id,
                incident_id=incident.incident_id,
                family_id=incident.family_id,
                variant_id=incident.variant_id,
                resolved=incident.mttr_seconds is not None,
                mttr_seconds=incident.mttr_seconds,
                decision_seconds=round(decision.decision_seconds, 3),
                actions_applied=applied,
                wrong_actions=sum(item == "no_effect" for item in outcomes),
                invalid_actions=invalid,
                first_action_correct=bool(outcomes) and outcomes[0] == "success",
                abstained=decision.abstained,
                escalated=any(
                    action.action_type in {"no_op_page_human", "escalate"}
                    for action in decision.actions
                ),
                failed_orders=len(orders),
                failed_order_value_cents=sum(
                    order.amount_cents for order in orders
                ),
                input_tokens=decision.input_tokens,
                output_tokens=decision.output_tokens,
                retrieved_memory_ids=tuple(
                    hit.memory_id for hit in decision.retrieved
                ),
                authorized_memory_id=decision.authorized_memory_id,
                invalid_plan=decision.invalid_plan,
            )
        )
    return results


def _summarize(results: list[RealIncidentResult]) -> dict[str, Any]:
    resolved = [item for item in results if item.resolved]
    mttr = [item.mttr_seconds for item in resolved]
    return {
        "incidents": len(results),
        "resolved": len(resolved),
        "resolution_rate": round(len(resolved) / len(results), 6)
        if results
        else 0.0,
        "median_mttr_seconds_resolved_only": statistics.median(mttr)
        if mttr
        else None,
        "mean_mttr_seconds_resolved_only": round(statistics.mean(mttr), 3)
        if mttr
        else None,
        "first_action_accuracy": round(
            statistics.mean(item.first_action_correct for item in results), 6
        )
        if results
        else 0.0,
        "wrong_actions": sum(item.wrong_actions for item in results),
        "invalid_actions": sum(item.invalid_actions for item in results),
        "invalid_plans": sum(item.invalid_plan for item in results),
        "escalations": sum(item.escalated for item in results),
        "failed_orders": sum(item.failed_orders for item in results),
        "failed_order_value_cents": sum(
            item.failed_order_value_cents for item in results
        ),
        "mean_decision_seconds": round(
            statistics.mean(item.decision_seconds for item in results), 3
        )
        if results
        else 0.0,
        "input_tokens_total": sum(item.input_tokens for item in results),
        "output_tokens_total": sum(item.output_tokens for item in results),
        "incidents_detail": [
            {
                **asdict(item),
                "retrieved_memory_ids": list(item.retrieved_memory_ids),
            }
            for item in results
        ],
    }


def _paired_delta(
    memory: list[RealIncidentResult], baseline: list[RealIncidentResult]
) -> dict[str, Any]:
    """Compare the arms without survivorship bias.

    Averaging MTTR over each arm's own resolved set is the classic trap: an
    arm that only resolves the easy incidents looks fast. So MTTR is compared
    **paired**, over the incidents *both* arms resolved, and the incidents
    only one arm resolved are reported separately as a resolution-rate delta.
    Neither number is meaningful without the other, so both are always emitted.
    """

    by_id_memory = {item.scenario_id: item for item in memory}
    by_id_baseline = {item.scenario_id: item for item in baseline}
    shared = sorted(set(by_id_memory) & set(by_id_baseline))

    both = [
        scenario
        for scenario in shared
        if by_id_memory[scenario].resolved and by_id_baseline[scenario].resolved
    ]
    memory_only = [
        scenario
        for scenario in shared
        if by_id_memory[scenario].resolved
        and not by_id_baseline[scenario].resolved
    ]
    baseline_only = [
        scenario
        for scenario in shared
        if by_id_baseline[scenario].resolved
        and not by_id_memory[scenario].resolved
    ]

    memory_mttr = [by_id_memory[scenario].mttr_seconds for scenario in both]
    baseline_mttr = [by_id_baseline[scenario].mttr_seconds for scenario in both]

    # Also compute with model latency removed, so a token-heavier prompt
    # cannot be mistaken for a slower remediation.
    memory_act = [
        by_id_memory[scenario].mttr_seconds
        - by_id_memory[scenario].decision_seconds
        for scenario in both
    ]
    baseline_act = [
        by_id_baseline[scenario].mttr_seconds
        - by_id_baseline[scenario].decision_seconds
        for scenario in both
    ]

    def _reduction(with_memory: list[float], without: list[float]) -> float | None:
        if not with_memory:
            return None
        mean_with = statistics.mean(with_memory)
        mean_without = statistics.mean(without)
        if mean_without == 0:
            return None
        return round((mean_without - mean_with) / mean_without * 100, 3)

    return {
        "method": (
            "MTTR compared pairwise over incidents BOTH arms resolved; "
            "incidents only one arm resolved are reported as a resolution-rate "
            "delta instead of being averaged away (no survivorship bias)."
        ),
        "incidents_in_stream": len(shared),
        "resolved_by_both": len(both),
        "resolved_by_memory_only": memory_only,
        "resolved_by_baseline_only": baseline_only,
        "resolution_rate_delta_points": round(
            (
                sum(item.resolved for item in memory)
                - sum(item.resolved for item in baseline)
            )
            / len(shared)
            * 100,
            3,
        )
        if shared
        else None,
        "paired_mean_mttr_seconds_with_memory": round(
            statistics.mean(memory_mttr), 3
        )
        if memory_mttr
        else None,
        "paired_mean_mttr_seconds_no_memory": round(
            statistics.mean(baseline_mttr), 3
        )
        if baseline_mttr
        else None,
        "mttr_reduction_percent": _reduction(memory_mttr, baseline_mttr),
        "mttr_reduction_percent_excluding_model_latency": _reduction(
            memory_act, baseline_act
        ),
        "first_action_accuracy_delta": round(
            statistics.mean(item.first_action_correct for item in memory)
            - statistics.mean(item.first_action_correct for item in baseline),
            6,
        )
        if memory and baseline
        else None,
        "wrong_action_delta": sum(item.wrong_actions for item in memory)
        - sum(item.wrong_actions for item in baseline),
        "failed_orders_avoided": sum(item.failed_orders for item in baseline)
        - sum(item.failed_orders for item in memory),
        "failed_order_value_avoided_cents": sum(
            item.failed_order_value_cents for item in baseline
        )
        - sum(item.failed_order_value_cents for item in memory),
    }


def run(
    *,
    region: str = DEFAULT_REGION,
    model_id: str = DEFAULT_MODEL_ID,
    retrieval_k: int = 5,
    client: Any | None = None,
    corpus_path: Path | str = FIXTURE_ROOT / "memory_corpus.json",
    scenario_path: Path | str | None = None,
    oracle_path: Path | str | None = None,
) -> dict[str, Any]:
    """Replay the incident stream through both arms and score the difference."""

    if client is None:
        client = _bedrock_client(region)

    # Both arms read the catalog from an identical fresh world -- built from
    # the same fixtures the arms will replay, so a custom scenario file cannot
    # hand the agent a catalog describing a different platform.
    catalog_kwargs: dict[str, Any] = {}
    if scenario_path is not None:
        catalog_kwargs["scenario_path"] = scenario_path
    if oracle_path is not None:
        catalog_kwargs["oracle_path"] = oracle_path
    catalog = _catalog(Conductor.from_files(**catalog_kwargs))

    def _make(arm: str) -> BedrockIncidentResponder:
        return BedrockIncidentResponder(
            arm=arm,
            client=client,
            model_id=model_id,
            catalog=catalog,
            corpus_path=corpus_path,
            retrieval_k=retrieval_k,
        )

    memory_results = _run_arm(
        _make("with_memory"),
        scenario_path=scenario_path,
        oracle_path=oracle_path,
    )
    baseline_results = _run_arm(
        _make("no_memory"),
        scenario_path=scenario_path,
        oracle_path=oracle_path,
    )

    if [item.scenario_id for item in memory_results] != [
        item.scenario_id for item in baseline_results
    ]:
        raise AssertionError("arms did not replay the same scenario stream")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "measured",
        "produced_by": PRODUCED_BY,
        "method": {
            "controlled_variable": "retrieved procedural memory in agent context",
            "model_id": model_id,
            "region": region,
            "retrieval_k": retrieval_k,
            "retriever": (
                "same text ranker scored by the `retrieval` section of "
                "postmortem_eval (hard negatives included) -- NOT production "
                "C-SPANN vector recall"
            ),
            "same_model_both_arms": True,
            "same_system_prompt_both_arms": True,
            "same_service_catalog_both_arms": True,
            "scenario_stream_identical": True,
            "baseline_kind": "same_model_no_memory",
            "temperature": 0.0,
            "abstention_is_correct_for_novel_family": True,
        },
        "arms": {
            "with_memory": _summarize(memory_results),
            "no_memory": _summarize(baseline_results),
        },
        "decision_quality": _paired_delta(memory_results, baseline_results),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Measure whether retrieved memory improves real-agent decisions. "
            "Requires AWS credentials and Bedrock model access."
        )
    )
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--retrieval-k", type=int, default=5)
    parser.add_argument(
        "--output",
        help="Write the report here as JSON; stdout is always emitted.",
    )
    args = parser.parse_args()

    report = run(
        region=args.region,
        model_id=args.model_id,
        retrieval_k=args.retrieval_k,
    )
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
