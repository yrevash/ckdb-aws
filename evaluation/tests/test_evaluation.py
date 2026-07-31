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
from postmortem_eval.responders import (
    DECISION_LATENCY_NOT_MEASURED,
    MemorylessBaselineResponder,
    ProceduralMemoryResponder,
)
from postmortem_sim import Conductor

EVAL_SRC = ROOT / "evaluation" / "postmortem_eval"


class RetrievalIsRealTests(unittest.TestCase):
    """Reality Charter §3 + fix E1: retrieval quality is a real property of the
    ranker over a corpus WITH hard negatives, measurable today without a model.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()
        cls.retrieval = cls.report["retrieval"]

    def test_retrieval_is_tagged_as_measured_with_provenance(self) -> None:
        self.assertEqual("measured", self.retrieval["status"])
        self.assertIn("postmortem_eval", self.retrieval["produced_by"])
        self.assertTrue(self.retrieval["corpus_has_hard_negatives"])
        self.assertGreaterEqual(self.retrieval["hard_negative_count"], 9)
        self.assertEqual(26, self.retrieval["queries"])

    def test_hard_negatives_drive_recall_at_1_below_perfect(self) -> None:
        # The whole point of E1: distractors are no longer trivially unrelated,
        # so the correct prior case is not always rank 1. recall@1 < 1.0 is the
        # honest, correct number -- not a bug.
        self.assertLess(self.retrieval["recall_at_1"], 1.0)
        self.assertGreater(self.retrieval["recall_at_1"], 0.5)
        self.assertLess(self.retrieval["ndcg_at_10"], 1.0)

    def test_a_hard_negative_actually_outranks_gold_somewhere(self) -> None:
        arms = self.report["decision_quality"]["mechanism_check"]["arms"]
        memory = arms["with_memory"]["incidents_detail"]
        stole_rank_one = [
            item["scenario_id"]
            for item in memory
            if item["retrieved_memory_ids"]
            and item["retrieved_memory_ids"][0].startswith("hardneg")
        ]
        self.assertTrue(stole_rank_one, "no hard negative ever reached rank 1")

    def test_recall_recovers_by_k_5_and_gold_is_never_authorized_wrongly(self) -> None:
        # A competent ranker still surfaces the gold within the top few, and the
        # deterministic memory arm still resolves every incident because non-gold
        # hard negatives carry no authorized action.
        self.assertEqual(1.0, self.retrieval["recall_at_5"])
        self.assertEqual(1.0, self.retrieval["recall_at_10"])
        memory = self.report["decision_quality"]["mechanism_check"]["arms"][
            "with_memory"
        ]["incidents_detail"]
        self.assertTrue(all(item["mttr_seconds"] is not None for item in memory))

    def test_novel_abstention_and_near_miss_rejection_are_real(self) -> None:
        self.assertGreaterEqual(self.retrieval["abstention_accuracy"], 0.95)
        # E6: the near-miss must be rejected by the real similarity threshold,
        # never authorizing the pool runbook, with no fixture-tuned phrase test.
        self.assertEqual(1.0, self.retrieval["near_miss_safe_rejection_accuracy"])
        self.assertEqual(
            0.0, self.retrieval["pool_runbook_near_miss_authorization_rate"]
        )

    def test_near_miss_rejection_is_not_an_oracle_aware_phrase_hack(self) -> None:
        # E6 regression guard: the fixture-tuned phrase must be gone from source.
        for name in ("responders.py", "runner.py"):
            source = (EVAL_SRC / name).read_text().lower()
            self.assertNotIn("pool health confirmed", source)

    def test_near_miss_incident_abstains_via_threshold(self) -> None:
        memory = self.report["decision_quality"]["mechanism_check"]["arms"][
            "with_memory"
        ]["incidents_detail"]
        near_miss = next(
            item
            for item in memory
            if item["variant_id"] == "red-herring-slow-query"
        )
        self.assertTrue(near_miss["abstained"])
        self.assertNotEqual("mem-f2-pool", near_miss["authorized_memory_id"])
        self.assertTrue(near_miss["first_action_correct"])


class CompetentBaselineTests(unittest.TestCase):
    """Fix E2: the memoryless baseline must be competent (Reality Charter R2),
    not a handicapped straw man that plays a wrong first action."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()
        cls.mech = cls.report["decision_quality"]["mechanism_check"]

    def test_baseline_is_labeled_competent_memoryless(self) -> None:
        self.assertEqual("competent_memoryless", self.mech["baseline_kind"])

    def test_baseline_plays_no_deliberately_wrong_first_action(self) -> None:
        self.assertTrue(
            self.mech["baseline_played_no_deliberately_wrong_first_action"]
        )
        baseline = self.mech["arms"]["competent_baseline"]
        # A competent baseline resolves the same stream with zero induced wrong
        # actions and correct-or-abstained first moves.
        self.assertEqual(0, baseline["wrong_actions"])
        self.assertGreaterEqual(baseline["first_action_accuracy"], 0.99)
        self.assertEqual(baseline["incidents"], baseline["resolved"])

    def test_baseline_is_not_worse_than_memory_arm_on_the_mechanism(self) -> None:
        # If a competent baseline already ties the memory arm on the toy
        # deterministic world, that is the finding -- and precisely why the
        # decision-quality claim is deferred to the real agent.
        memory = self.mech["arms"]["with_memory"]
        baseline = self.mech["arms"]["competent_baseline"]
        self.assertGreaterEqual(
            baseline["first_action_accuracy"], memory["first_action_accuracy"]
        )
        self.assertLessEqual(baseline["wrong_actions"], memory["wrong_actions"])

    def test_baseline_source_has_no_hardcoded_wrong_action(self) -> None:
        source = (EVAL_SRC / "responders.py").read_text()
        self.assertNotIn("wrong_scale", source)
        self.assertNotIn("wrong_restart", source)


class DecisionQualityIsPendingTests(unittest.TestCase):
    """Fix R7: MTTR delta, first-action accuracy and wrong-action rate require
    the real Bedrock agent; they must be tagged pending, never emitted as a
    measured improvement headline."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()
        cls.dq = cls.report["decision_quality"]

    def test_decision_quality_is_tagged_pending_real_agent_run(self) -> None:
        self.assertEqual("pending_real_agent_run", self.dq["status"])
        self.assertFalse(self.dq["measured"])

    def test_no_mttr_improvement_headline_is_emitted(self) -> None:
        self.assertIsNone(self.dq["mttr_reduction_percent"])
        self.assertIsNone(self.dq["first_action_accuracy_delta"])
        self.assertIsNone(self.dq["wrong_action_rate_delta"])
        self.assertIsNone(self.dq["failed_orders_avoided"])
        # The old rigged top-level improvement block must be gone entirely.
        self.assertNotIn("comparison", self.report)

    def test_deterministic_arms_are_only_a_mechanism_check(self) -> None:
        mech = self.dq["mechanism_check"]
        self.assertTrue(mech["scenario_stream_identical"])
        self.assertTrue(mech["deterministic_ids_match"])
        self.assertTrue(mech["both_arms_resolved_all_incidents"])
        self.assertIn("NOT a performance comparison", mech["note"])


class NoHardcodedLearningCurveTests(unittest.TestCase):
    """Fix E3: the per-occurrence improvement came from a hardcoded
    decision_seconds=(180, 60, 0) tuple. It must be gone; any learning effect
    must emerge or not be claimed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()

    def test_hardcoded_decision_time_ramp_is_removed_from_source(self) -> None:
        for name in ("responders.py", "runner.py"):
            source = (EVAL_SRC / name).read_text()
            self.assertNotIn("(180, 60, 0)", source)
            self.assertNotIn("180, 60, 0", source)
        self.assertEqual(0, DECISION_LATENCY_NOT_MEASURED)

    def test_no_fabricated_learning_curve_is_published(self) -> None:
        self.assertNotIn("learning_curve", self.report)
        self.assertNotIn("decision_time_model", self.report["method"])

    def test_memory_arm_mttr_is_flat_across_occurrences(self) -> None:
        # With the ramp gone, MTTR depends only on the (fixed) action count for
        # a family/variant, never on how many times it has been seen. So for a
        # given variant, MTTR must be identical across occurrences -- proof that
        # no per-occurrence improvement was fabricated.
        memory = self.report["decision_quality"]["mechanism_check"]["arms"][
            "with_memory"
        ]["incidents_detail"]
        by_variant: dict[tuple[str, str], set[int]] = {}
        for item in memory:
            by_variant.setdefault(
                (item["family_id"], item["variant_id"]), set()
            ).add(item["mttr_seconds"])
        for key, mttrs in by_variant.items():
            self.assertEqual(1, len(mttrs), (key, mttrs))


class HarnessInvariantsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()

    def test_occurrence_invariant_matches_research(self) -> None:
        incidents = self.report["decision_quality"]["mechanism_check"]["arms"][
            "with_memory"
        ]["incidents_detail"]
        families: dict[str, int] = {}
        for incident in incidents:
            families[incident["family_id"]] = (
                families.get(incident["family_id"], 0) + 1
            )
        self.assertEqual(10, len(families))
        self.assertEqual(2, families["F10_NOVEL"])
        self.assertTrue(
            all(
                count >= 3
                for family, count in families.items()
                if family != "F10_NOVEL"
            ),
            families,
        )

    def test_arms_replay_identical_scenarios_and_ids(self) -> None:
        arms = self.report["decision_quality"]["mechanism_check"]["arms"]
        memory = arms["with_memory"]["incidents_detail"]
        baseline = arms["competent_baseline"]["incidents_detail"]
        self.assertEqual(
            [(item["scenario_id"], item["incident_id"]) for item in memory],
            [(item["scenario_id"], item["incident_id"]) for item in baseline],
        )
        self.assertTrue(self.report["method"]["scenario_stream_identical"])

    def test_report_is_stable_and_json_serializable(self) -> None:
        again = EvaluationHarness().run()
        self.assertEqual(
            json.dumps(self.report, sort_keys=True),
            json.dumps(again, sort_keys=True),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            written = EvaluationHarness().write_json(output)
            self.assertEqual(written, json.loads(output.read_text()))

    def test_responder_observation_does_not_expose_oracle_labels(self) -> None:
        conductor = Conductor.from_files()
        incident = conductor.inject_next()
        observation = conductor.observe_incident(incident.incident_id)
        self.assertFalse(hasattr(observation, "family_id"))
        self.assertFalse(hasattr(observation, "variant_id"))
        self.assertFalse(hasattr(observation, "required_actions"))

    def test_protocol_responders_do_not_load_hidden_oracle(self) -> None:
        memory = ProceduralMemoryResponder()
        baseline = MemorylessBaselineResponder()
        self.assertFalse(hasattr(memory, "_oracle"))
        self.assertFalse(hasattr(baseline, "_oracle"))


if __name__ == "__main__":
    unittest.main()
