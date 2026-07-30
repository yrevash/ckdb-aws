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
    ColdStartResponder,
    ProceduralMemoryResponder,
)
from postmortem_sim import Conductor


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()

    def test_occurrence_invariant_matches_research(self) -> None:
        incidents = self.report["arms"]["with_memory"]["incidents"]
        families: dict[str, int] = {}
        for incident in incidents:
            families[incident["family_id"]] = families.get(incident["family_id"], 0) + 1
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
        memory = self.report["arms"]["with_memory"]["incidents"]
        cold = self.report["arms"]["cold_start"]["incidents"]
        self.assertEqual(
            [(item["scenario_id"], item["incident_id"]) for item in memory],
            [(item["scenario_id"], item["incident_id"]) for item in cold],
        )
        self.assertTrue(self.report["method"]["scenario_stream_identical"])

    def test_memory_improves_objective_operational_metrics(self) -> None:
        memory = self.report["arms"]["with_memory"]["summary"]
        cold = self.report["arms"]["cold_start"]["summary"]
        self.assertLess(memory["median_mttr_seconds"], cold["median_mttr_seconds"])
        self.assertLess(memory["wrong_actions"], cold["wrong_actions"])
        self.assertLess(memory["failed_orders"], cold["failed_orders"])
        self.assertLess(
            memory["failed_order_value_cents"],
            cold["failed_order_value_cents"],
        )
        self.assertGreater(
            memory["first_action_accuracy"], cold["first_action_accuracy"]
        )
        self.assertLess(memory["token_proxy_total"], cold["token_proxy_total"])
        self.assertLess(memory["cost_proxy_usd"], cold["cost_proxy_usd"])

    def test_recall_and_abstention_targets_are_met(self) -> None:
        recall = self.report["recall"]
        self.assertGreaterEqual(recall["recall_at_10"], 0.95)
        self.assertGreaterEqual(recall["abstention_accuracy"], 0.95)
        self.assertEqual(26, recall["queries"])
        self.assertEqual(0.1, recall["precision_at_10"])
        self.assertGreaterEqual(recall["ndcg_at_10"], 0.95)
        self.assertEqual(1.0, recall["near_miss_safe_rejection_accuracy"])
        self.assertEqual(
            0.0,
            recall["pool_runbook_near_miss_authorization_rate"],
        )

    def test_learning_curve_reports_each_known_occurrence(self) -> None:
        for arm in ("with_memory", "cold_start"):
            curve = self.report["learning_curve"][arm]
            self.assertEqual([1, 2, 3], [item["occurrence"] for item in curve])
            self.assertEqual([9, 9, 9], [item["incidents"] for item in curve])
            for item in curve:
                self.assertIn("median_mttr_seconds", item)
                self.assertIn("first_action_accuracy", item)
                self.assertIn("mean_actions_to_resolution", item)
        memory_mttr = [
            item["median_mttr_seconds"]
            for item in self.report["learning_curve"]["with_memory"]
        ]
        cold_mttr = [
            item["median_mttr_seconds"]
            for item in self.report["learning_curve"]["cold_start"]
        ]
        self.assertEqual(sorted(memory_mttr, reverse=True), memory_mttr)
        self.assertLess(memory_mttr[-1], memory_mttr[0])
        self.assertEqual(1, len(set(cold_mttr)))

    def test_near_miss_does_not_authorize_pool_runbook(self) -> None:
        near_miss = next(
            item
            for item in self.report["arms"]["with_memory"]["incidents"]
            if item["variant_id"] == "red-herring-slow-query"
        )
        self.assertTrue(near_miss["abstained"])
        self.assertNotEqual("mem-f2-pool", near_miss["authorized_memory_id"])
        self.assertTrue(near_miss["first_action_correct"])

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
        cold = ColdStartResponder()
        self.assertFalse(hasattr(memory, "_oracle"))
        self.assertFalse(hasattr(cold, "_oracle"))


if __name__ == "__main__":
    unittest.main()
