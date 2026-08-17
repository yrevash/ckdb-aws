"""Mechanism tests for the real-agent decision-quality harness.

Reality Charter R8: the Bedrock client is doubled **here only**. These tests
prove the harness is fair, leak-free and crash-proof; they do NOT produce or
validate any decision-quality figure. The published number may only come from
`python -m postmortem_eval.real_agent` against real Bedrock, and this file
asserts that the pending/measured switch cannot be flipped by anything else.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simulator"))
sys.path.insert(0, str(ROOT / "evaluation"))

from postmortem_eval import EvaluationHarness
from postmortem_eval.real_agent import (
    ACTION_SCHEMA,
    BedrockIncidentResponder,
    _catalog,
    run,
)
from postmortem_sim import Conductor


def _prompt_of(call: dict) -> str:
    return call["messages"][0]["content"][0]["text"]


class ScriptedBedrock:
    """A Converse-shaped double. Records every request for inspection."""

    def __init__(self, *, plan_from_memory: bool = True) -> None:
        self.calls: list[dict] = []
        self.plan_from_memory = plan_from_memory

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        prompt = _prompt_of(kwargs)
        body = self._plan(prompt)
        return {
            "output": {"message": {"content": [{"text": json.dumps(body)}]}},
            "usage": {"inputTokens": 1234, "outputTokens": 56, "totalTokens": 1290},
        }

    def _plan(self, prompt: str) -> dict:
        memories = self._memories(prompt)
        if self.plan_from_memory:
            for memory in memories:
                if memory["runbook_actions"]:
                    return {
                        "reasoning": "matched a prior incident",
                        "actions": memory["runbook_actions"],
                        "cited_memory_id": memory["memory_id"],
                    }
        return {
            "reasoning": "no confident match",
            "actions": [
                {
                    "action_type": "no_op_page_human",
                    "target_service": "checkout-svc",
                    "params": {},
                }
            ],
            "cited_memory_id": None,
        }

    @staticmethod
    def _memories(prompt: str) -> list[dict]:
        marker = "## Retrieved prior incidents"
        if marker not in prompt:
            return []
        tail = prompt.split(marker, 1)[1]
        start = tail.index("[")
        return json.loads(tail[start:])


class PairedBedrock(ScriptedBedrock):
    """Lets BOTH arms resolve, so the paired MTTR math actually runs.

    The memory arm plays the retrieved runbook. The baseline replays the same
    final action for the same incident but wastes one wrong step first --
    standing in for "gets there eventually, less directly". Arms run
    memory-first, so keying the replay on the incident title is sufficient.
    """

    def __init__(self) -> None:
        super().__init__()
        self.learned: dict[str, list[dict]] = {}

    def _plan(self, prompt: str) -> dict:
        title = prompt.split('"title": "', 1)[1].split('"', 1)[0]
        memories = self._memories(prompt)
        if memories:
            for memory in memories:
                if memory["runbook_actions"]:
                    self.learned[title] = memory["runbook_actions"]
                    return {
                        "reasoning": "matched a prior incident",
                        "actions": memory["runbook_actions"],
                        "cited_memory_id": memory["memory_id"],
                    }
            return super()._plan(prompt)

        actions = self.learned.get(title)
        if not actions:
            return super()._plan(prompt)
        wasted = {
            "action_type": "restart_service",
            "target_service": actions[0]["target_service"],
            "params": {},
        }
        return {
            "reasoning": "trying the usual first-line fix",
            "actions": [wasted, *actions],
            "cited_memory_id": None,
        }


class MisbehavingBedrock(ScriptedBedrock):
    """Returns plans the platform cannot execute, and one that is not JSON."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.mode == "not_json":
            payload = {"text": "I think we should probably restart something."}
        elif self.mode == "unknown_action":
            payload = {
                "text": json.dumps(
                    {
                        "actions": [
                            {
                                "action_type": "reboot_the_datacenter",
                                "target_service": "checkout-svc",
                                "params": {},
                            }
                        ],
                        "cited_memory_id": None,
                    }
                )
            }
        elif self.mode == "bad_params":
            payload = {
                "text": json.dumps(
                    {
                        "actions": [
                            {
                                "action_type": "scale_service",
                                "target_service": "checkout-svc",
                                "params": {"capacity_units": -4},
                            }
                        ],
                        "cited_memory_id": None,
                    }
                )
            }
        else:  # hallucinated citation
            payload = {
                "text": json.dumps(
                    {
                        "actions": [
                            {
                                "action_type": "restart_service",
                                "target_service": "checkout-svc",
                                "params": {},
                            }
                        ],
                        "cited_memory_id": "mem-does-not-exist",
                    }
                )
            }
        return {
            "output": {"message": {"content": [payload]}},
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }


class ArmFairnessTests(unittest.TestCase):
    """Reality Charter R2: the only difference between arms is the memory."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = ScriptedBedrock()
        cls.report = run(client=cls.client, model_id="test-model")
        half = len(cls.client.calls) // 2
        cls.memory_calls = cls.client.calls[:half]
        cls.baseline_calls = cls.client.calls[half:]

    def test_both_arms_use_the_same_model_and_settings(self) -> None:
        models = {call["modelId"] for call in self.client.calls}
        self.assertEqual({"test-model"}, models)
        temps = {
            call["inferenceConfig"]["temperature"] for call in self.client.calls
        }
        self.assertEqual({0.0}, temps)
        systems = {call["system"][0]["text"] for call in self.client.calls}
        self.assertEqual(1, len(systems), "system prompt differed between arms")

    def test_both_arms_replay_the_same_stream(self) -> None:
        self.assertEqual(len(self.memory_calls), len(self.baseline_calls))
        self.assertTrue(self.report["method"]["scenario_stream_identical"])
        memory_detail = self.report["arms"]["with_memory"]["incidents_detail"]
        baseline_detail = self.report["arms"]["no_memory"]["incidents_detail"]
        self.assertEqual(
            [item["scenario_id"] for item in memory_detail],
            [item["scenario_id"] for item in baseline_detail],
        )

    def test_only_the_memory_arm_receives_memories(self) -> None:
        for call in self.memory_calls:
            self.assertIn("## Retrieved prior incidents", _prompt_of(call))
        for call in self.baseline_calls:
            self.assertNotIn("## Retrieved prior incidents", _prompt_of(call))
            self.assertNotIn("runbook_actions", _prompt_of(call))

    def test_both_arms_receive_the_same_catalog_and_action_schema(self) -> None:
        catalog = _catalog(Conductor.from_files())
        for call in self.client.calls:
            prompt = _prompt_of(call)
            self.assertIn(catalog, prompt)
            self.assertIn(ACTION_SCHEMA, prompt)

    def test_baseline_is_not_handicapped_and_may_abstain(self) -> None:
        # It gets the same escalation affordance the memory arm has.
        for call in self.baseline_calls:
            self.assertIn("no_op_page_human", _prompt_of(call))


class NoLeakageTests(unittest.TestCase):
    """Reality Charter R3: no oracle, family label, or answer key in context."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = ScriptedBedrock()
        run(client=cls.client, model_id="test-model")
        cls.prompts = [_prompt_of(call) for call in cls.client.calls]

    def test_family_ids_never_appear_in_any_prompt(self) -> None:
        fixture = json.loads(
            (ROOT / "simulator" / "fixtures" / "scenarios.json").read_text()
        )
        families = {item["family_id"] for item in fixture["scenarios"]}
        for prompt in self.prompts:
            for family in families:
                self.assertNotIn(family, prompt)

    def test_oracle_vocabulary_never_appears_in_any_prompt(self) -> None:
        for prompt in self.prompts:
            for token in (
                "required_actions",
                "wrong_action_penalty_seconds",
                "action_seconds",
                "oracle",
                "variant_id",
            ):
                self.assertNotIn(token, prompt)

    def test_hard_negatives_are_not_filtered_out_of_retrieval(self) -> None:
        # The memory arm must face the same imperfect ranking the retrieval
        # section scores -- filtering look-alikes here would inflate MTTR.
        joined = "\n".join(self.prompts)
        self.assertIn("hardneg-", joined)


class ResilienceTests(unittest.TestCase):
    """A misbehaving agent is data, not a crash."""

    def _one_incident(self, client) -> object:
        conductor = Conductor.from_files()
        responder = BedrockIncidentResponder(
            arm="no_memory",
            client=client,
            model_id="test-model",
            catalog=_catalog(conductor),
        )
        incident = conductor.inject_next()
        return responder.decide(conductor.observe_incident(incident.incident_id))

    def test_unparseable_output_escalates_instead_of_raising(self) -> None:
        decision = self._one_incident(MisbehavingBedrock("not_json"))
        self.assertTrue(decision.invalid_plan)
        self.assertTrue(decision.abstained)
        self.assertEqual("no_op_page_human", decision.actions[0].action_type)

    def test_unknown_action_is_recorded_not_raised(self) -> None:
        from postmortem_eval.real_agent import _run_arm

        conductor = Conductor.from_files()
        responder = BedrockIncidentResponder(
            arm="no_memory",
            client=MisbehavingBedrock("unknown_action"),
            model_id="test-model",
            catalog=_catalog(conductor),
        )
        results = _run_arm(responder)
        self.assertTrue(all(not item.resolved for item in results))
        self.assertTrue(all(item.mttr_seconds is None for item in results))
        self.assertTrue(sum(item.invalid_actions for item in results) > 0)

    def test_invalid_parameters_are_recorded_not_raised(self) -> None:
        from postmortem_eval.real_agent import _run_arm

        conductor = Conductor.from_files()
        responder = BedrockIncidentResponder(
            arm="no_memory",
            client=MisbehavingBedrock("bad_params"),
            model_id="test-model",
            catalog=_catalog(conductor),
        )
        results = _run_arm(responder)
        self.assertTrue(sum(item.invalid_actions for item in results) > 0)

    def test_hallucinated_citation_is_dropped(self) -> None:
        conductor = Conductor.from_files()
        responder = BedrockIncidentResponder(
            arm="with_memory",
            client=MisbehavingBedrock("hallucinated_citation"),
            model_id="test-model",
            catalog=_catalog(conductor),
        )
        incident = conductor.inject_next()
        decision = responder.decide(
            conductor.observe_incident(incident.incident_id)
        )
        self.assertIsNone(
            decision.authorized_memory_id,
            "a memory_id that was never retrieved must not count as grounding",
        )


class MeasurementProvenanceTests(unittest.TestCase):
    """Every emitted number is real and tagged with how it was produced."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = ScriptedBedrock()
        cls.report = run(client=cls.client, model_id="test-model")

    def test_token_counts_come_from_the_api_usage_block(self) -> None:
        arm = self.report["arms"]["with_memory"]
        self.assertEqual(1234 * arm["incidents"], arm["input_tokens_total"])
        self.assertEqual(56 * arm["incidents"], arm["output_tokens_total"])

    def test_decision_latency_is_measured_not_assumed(self) -> None:
        for name in ("with_memory", "no_memory"):
            arm = self.report["arms"][name]
            self.assertGreaterEqual(arm["mean_decision_seconds"], 0.0)
            for incident in arm["incidents_detail"]:
                self.assertIsInstance(incident["decision_seconds"], float)

    def test_report_is_tagged_with_provenance_and_method(self) -> None:
        self.assertEqual("measured", self.report["status"])
        self.assertIn("real_agent.py", self.report["produced_by"])
        method = self.report["method"]
        self.assertEqual(
            "retrieved procedural memory in agent context",
            method["controlled_variable"],
        )
        self.assertTrue(method["same_model_both_arms"])
        self.assertIn("NOT production", method["retriever"])


class PairedComparisonTests(unittest.TestCase):
    """MTTR is compared without survivorship bias."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run(client=ScriptedBedrock(), model_id="test-model")
        cls.delta = cls.report["decision_quality"]

    def test_mttr_is_paired_over_incidents_both_arms_resolved(self) -> None:
        self.assertIn("resolved_by_both", self.delta)
        self.assertIn("pairwise", self.delta["method"])
        both = self.delta["resolved_by_both"]
        self.assertLessEqual(
            both, self.report["arms"]["with_memory"]["resolved"]
        )
        self.assertLessEqual(both, self.report["arms"]["no_memory"]["resolved"])

    def test_one_sided_resolutions_are_surfaced_not_averaged_away(self) -> None:
        self.assertIsInstance(self.delta["resolved_by_memory_only"], list)
        self.assertIsInstance(self.delta["resolved_by_baseline_only"], list)
        self.assertIsNotNone(self.delta["resolution_rate_delta_points"])

    def test_latency_decomposition_is_reported(self) -> None:
        # A memory-heavier prompt must not be mistakable for slower repair.
        self.assertIn(
            "mttr_reduction_percent_excluding_model_latency", self.delta
        )

    def test_no_paired_set_means_no_fabricated_mttr_delta(self) -> None:
        # This double never lets both arms resolve the same incident, so
        # there is nothing legitimate to compare. The harness must say so
        # rather than averaging each arm's own survivors against each other.
        self.assertEqual(0, self.delta["resolved_by_both"])
        self.assertIsNone(self.delta["mttr_reduction_percent"])
        self.assertIsNone(self.delta["paired_mean_mttr_seconds_with_memory"])
        # The resolution-rate delta is still real and still reported.
        self.assertIsNotNone(self.delta["resolution_rate_delta_points"])


class PairedMathTests(unittest.TestCase):
    """The reduction arithmetic itself, on a stream both arms resolve."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = run(client=PairedBedrock(), model_id="test-model")
        cls.delta = cls.report["decision_quality"]

    def test_both_arms_resolve_a_shared_set(self) -> None:
        self.assertGreater(self.delta["resolved_by_both"], 0)

    def test_reduction_is_computed_over_the_shared_set(self) -> None:
        with_memory = self.delta["paired_mean_mttr_seconds_with_memory"]
        no_memory = self.delta["paired_mean_mttr_seconds_no_memory"]
        self.assertIsNotNone(with_memory)
        self.assertIsNotNone(no_memory)
        expected = round((no_memory - with_memory) / no_memory * 100, 3)
        self.assertAlmostEqual(
            expected, self.delta["mttr_reduction_percent"], places=3
        )

    def test_a_wasted_step_shows_up_as_a_positive_reduction(self) -> None:
        # The baseline burns one wrong action (a real penalty in the world
        # model), so memory must come out ahead on the shared set.
        self.assertGreater(self.delta["mttr_reduction_percent"], 0)
        self.assertLess(self.delta["wrong_action_delta"], 0)


class ReportWiringTests(unittest.TestCase):
    """The pending/measured switch can only be flipped by a real run."""

    def test_default_report_still_reports_pending(self) -> None:
        report = EvaluationHarness().run()
        self.assertEqual(
            "pending_real_agent_run", report["decision_quality"]["status"]
        )
        self.assertIsNone(report["decision_quality"]["mttr_reduction_percent"])

    def test_supplying_a_measured_report_embeds_it_with_its_source(self) -> None:
        measured = run(client=ScriptedBedrock(), model_id="test-model")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision-quality.json"
            path.write_text(json.dumps(measured))
            report = EvaluationHarness(decision_quality_path=path).run()
        block = report["decision_quality"]
        self.assertEqual("measured", block["status"])
        self.assertTrue(block["measured"])
        self.assertEqual(str(path), block["source_report"])
        self.assertIn("real_agent.py", block["produced_by"])

    def test_a_missing_report_is_an_error_not_a_silent_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = EvaluationHarness(
                decision_quality_path=Path(tmp) / "absent.json"
            )
            with self.assertRaises(FileNotFoundError):
                harness.run()

    def test_a_non_measured_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stub.json"
            path.write_text(
                json.dumps(
                    {"status": "pending_real_agent_run", "decision_quality": {}}
                )
            )
            with self.assertRaises(ValueError):
                EvaluationHarness(decision_quality_path=path).run()


if __name__ == "__main__":
    unittest.main()
