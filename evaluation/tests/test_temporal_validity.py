"""Phase 3 / Track B: temporal-validity evaluation metric tests.

Exercises EvaluationHarness's additive "temporal_drift" report section,
which replays the two temporal-drift families (F11_POOL_DRIVER_MIGRATION,
F12_CACHE_TOPOLOGY_MIGRATION) against a bitemporal-aware procedural-memory
responder and scores whether it applies the currently-valid fix rather than a
stale one. Runs entirely against isolated drift fixtures -- see
evaluation/tests/test_evaluation.py for the Phase 2 default-fixture contract,
which this suite does not touch or perturb.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simulator"))
sys.path.insert(0, str(ROOT / "evaluation"))

from postmortem_eval import EvaluationHarness  # noqa: E402
from postmortem_eval.responders import ProceduralMemoryResponder  # noqa: E402
from postmortem_eval.runner import (  # noqa: E402
    DRIFT_CORPUS_PATH,
    DRIFT_ORACLE_PATH,
    DRIFT_SCENARIO_PATH,
    STALE_FACT_TARGET,
    TEMPORAL_VALIDITY_TARGET,
    TemporalProceduralMemoryResponder,
    _is_valid_at,
    _parse_ts,
)
from postmortem_sim import Conductor  # noqa: E402


class TemporalValidityMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = EvaluationHarness().run()

    def test_phase2_default_report_shape_is_unperturbed(self) -> None:
        """The additive temporal_drift section must not have changed any
        existing top-level key or the Phase 2 arms' exact values -- those are
        covered in detail by evaluation/tests/test_evaluation.py, this is a
        sentinel that the keys are still all present alongside the new one.
        """

        for key in (
            "schema_version",
            "seed",
            "method",
            "recall",
            "learning_curve",
            "arms",
            "comparison",
        ):
            self.assertIn(key, self.report)
        self.assertIn("temporal_drift", self.report)

    def test_temporal_validity_meets_the_phase3_target(self) -> None:
        drift = self.report["temporal_drift"]
        self.assertGreaterEqual(
            drift["temporal_validity_accuracy"], TEMPORAL_VALIDITY_TARGET
        )
        self.assertEqual(STALE_FACT_TARGET, drift["stale_fact_applications"])
        self.assertTrue(drift["meets_target"])
        self.assertEqual(0.90, TEMPORAL_VALIDITY_TARGET)

    def test_both_drift_families_are_represented(self) -> None:
        drift = self.report["temporal_drift"]
        self.assertEqual(
            ["F11_POOL_DRIVER_MIGRATION", "F12_CACHE_TOPOLOGY_MIGRATION"],
            drift["families"],
        )
        self.assertEqual(4, drift["incidents_evaluated"])
        self.assertEqual(4, len(drift["incidents"]))

    def test_every_drift_incident_resolved_and_used_its_currently_valid_fix(self) -> None:
        for incident in self.report["temporal_drift"]["incidents"]:
            with self.subTest(scenario_id=incident["scenario_id"]):
                self.assertTrue(incident["resolved"], incident)
                self.assertTrue(incident["applied_currently_valid_fix"], incident)
                self.assertFalse(incident["applied_stale_fact"], incident)
                self.assertEqual(
                    incident["authorized_memory_id"], incident["expected_memory_id"]
                )

    def test_post_migration_incidents_do_not_authorize_the_pre_migration_memory(self) -> None:
        drift = self.report["temporal_drift"]
        by_scenario = {item["scenario_id"]: item for item in drift["incidents"]}
        self.assertEqual(
            "mem-f11-multiplexed-pool",
            by_scenario["f11-pool-migrated"]["authorized_memory_id"],
        )
        self.assertEqual(
            "mem-f12-managed-cache",
            by_scenario["f12-cache-managed"]["authorized_memory_id"],
        )

    def test_report_is_deterministic_across_runs(self) -> None:
        again = EvaluationHarness().run()
        self.assertEqual(
            json.dumps(self.report["temporal_drift"], sort_keys=True),
            json.dumps(again["temporal_drift"], sort_keys=True),
        )


class TemporalValidityFilterUnitTests(unittest.TestCase):
    """Directly unit-tests the bitemporal filter helpers/responder, isolated
    from the full harness, so a future change to family count or corpus size
    doesn't obscure *why* a validity assertion failed.
    """

    def test_is_valid_at_mirrors_the_sql_gate(self) -> None:
        window = (_parse_ts("2026-07-01T00:00:00Z"), _parse_ts("2026-07-02T00:00:00Z"))
        self.assertFalse(_is_valid_at(window, _parse_ts("2026-06-30T23:59:59Z")))
        self.assertTrue(_is_valid_at(window, _parse_ts("2026-07-01T00:00:00Z")))
        self.assertTrue(_is_valid_at(window, _parse_ts("2026-07-01T12:00:00Z")))
        self.assertFalse(_is_valid_at(window, _parse_ts("2026-07-02T00:00:00Z")))
        # No as_of supplied -> permissive (matches production default reads).
        self.assertTrue(_is_valid_at(window, None))
        # No window on the record -> always eligible (Phase 2 corpus parity).
        self.assertTrue(_is_valid_at((None, None), _parse_ts("2026-07-01T00:00:00Z")))

    def test_temporal_responder_excludes_the_not_yet_valid_replacement_fix(self) -> None:
        responder = TemporalProceduralMemoryResponder(DRIFT_CORPUS_PATH, retrieval_k=10)
        conductor = Conductor(
            json.loads(DRIFT_SCENARIO_PATH.read_text()),
            json.loads(DRIFT_ORACLE_PATH.read_text()),
        )
        incident = conductor.inject_next()  # f11-pool-legacy, pre-migration
        observation = conductor.observe_incident(incident.incident_id)

        decision = responder.decide(observation)

        self.assertEqual("mem-f11-legacy-pool", decision.authorized_memory_id)
        self.assertNotIn(
            "mem-f11-multiplexed-pool",
            [hit.memory_id for hit in decision.retrieved],
        )

    def test_temporal_responder_is_a_behavioral_no_op_on_the_phase2_corpus(self) -> None:
        """Every Phase 2 corpus record has no valid_from/valid_to, so the
        temporal-aware responder must retrieve and authorize exactly what the
        base (non-temporal) ProceduralMemoryResponder does, request for
        request -- this is what makes swapping the response class safe for
        Track B without touching responders.py or perturbing Phase 2 numbers.
        """

        from postmortem_eval.runner import FIXTURE_ROOT

        conductor = Conductor.from_files()
        baseline = ProceduralMemoryResponder()
        temporal = TemporalProceduralMemoryResponder(
            FIXTURE_ROOT / "memory_corpus.json"
        )
        for _ in range(6):
            incident = conductor.inject_next()
            observation = conductor.observe_incident(incident.incident_id)
            baseline_decision = baseline.decide(observation)
            temporal_decision = temporal.decide(observation)
            with self.subTest(scenario_id=incident.scenario_id):
                self.assertEqual(
                    baseline_decision.authorized_memory_id,
                    temporal_decision.authorized_memory_id,
                )
                self.assertEqual(
                    [hit.memory_id for hit in baseline_decision.retrieved],
                    [hit.memory_id for hit in temporal_decision.retrieved],
                )
                self.assertEqual(
                    baseline_decision.abstained, temporal_decision.abstained
                )
            for action in baseline_decision.actions:
                conductor.apply_action(
                    incident.incident_id,
                    action.action_type,
                    action.target_service,
                    action.params,
                )


if __name__ == "__main__":
    unittest.main()
