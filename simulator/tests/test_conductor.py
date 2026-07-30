from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


SIMULATOR_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIMULATOR_ROOT))

from postmortem_sim import Conductor, Health, IncidentStatus, SimulationError


class ConductorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conductor = Conductor.from_files()

    def test_replay_is_byte_for_byte_deterministic(self) -> None:
        first = Conductor.from_files()
        second = Conductor.from_files()
        for _ in range(3):
            first.inject_next()
            second.inject_next()

        first_snapshot = json.dumps(first.state.snapshot(), sort_keys=True)
        second_snapshot = json.dumps(second.state.snapshot(), sort_keys=True)
        self.assertEqual(first_snapshot, second_snapshot)

    def test_fault_injection_opens_incident_breaches_slo_and_fails_orders(self) -> None:
        incident = self.conductor.inject_next()

        self.assertEqual(IncidentStatus.OPEN, incident.status)
        self.assertEqual(Health.DEGRADED, self.conductor.state.services["checkout-svc"].health)
        self.assertEqual("breaching", self.conductor.state.slos[("checkout-svc", "error_rate")].status)
        self.assertEqual(6, len(self.conductor.state.orders))
        self.assertTrue(
            any(
                alert.incident_id == incident.incident_id
                and alert.cleared_at is None
                for alert in self.conductor.state.alerts.values()
            )
        )

    def test_wrong_action_keeps_bad_deploy_open_and_adds_business_impact(self) -> None:
        incident = self.conductor.inject_next()
        initial_orders = len(self.conductor.state.orders)

        result = self.conductor.apply_action(
            incident.incident_id,
            "scale_service",
            "checkout-svc",
            {"capacity_units": 10},
        )

        self.assertFalse(result.resolved)
        self.assertEqual("no_effect", result.outcome)
        self.assertEqual(IncidentStatus.OPEN, incident.status)
        self.assertEqual(Health.DEGRADED, self.conductor.state.services["checkout-svc"].health)
        self.assertEqual(initial_orders + 2, len(self.conductor.state.orders))

    def test_rollback_resolves_bad_deploy_and_clears_alert(self) -> None:
        incident = self.conductor.inject_next()
        memory_ref = "7bb59685-0360-446a-a853-e425e0de472e"

        result = self.conductor.apply_action(
            incident.incident_id,
            "rollback_deploy",
            "checkout-svc",
            {"target_version": "1.4.2"},
            memory_ref=memory_ref,
        )

        self.assertTrue(result.resolved)
        self.assertEqual(IncidentStatus.RESOLVED, incident.status)
        self.assertEqual(120, incident.mttr_seconds)
        self.assertEqual("1.4.2", self.conductor.state.services["checkout-svc"].current_version)
        self.assertEqual(Health.HEALTHY, self.conductor.state.services["checkout-svc"].health)
        self.assertEqual(memory_ref, self.conductor.state.actions[-1].memory_ref)
        self.assertTrue(
            all(
                alert.cleared_at is not None
                for alert in self.conductor.state.alerts.values()
                if alert.incident_id == incident.incident_id
            )
        )

    def test_pool_exhaustion_requires_config_change_then_restart(self) -> None:
        first = self.conductor.inject_next()
        self.conductor.apply_action(
            first.incident_id,
            "rollback_deploy",
            "checkout-svc",
            {"target_version": "1.4.2"},
        )
        incident = self.conductor.inject_next()

        config_result = self.conductor.apply_action(
            incident.incident_id,
            "set_config",
            "checkout-svc",
            {"key": "db_pool_size", "value": 80},
        )
        self.assertFalse(config_result.resolved)
        self.assertEqual(IncidentStatus.MITIGATING, incident.status)
        self.assertEqual(
            80,
            self.conductor.state.current_config("checkout-svc", "db_pool_size").value,
        )
        prior = [
            revision
            for revision in self.conductor.state.configs
            if revision.service == "checkout-svc"
            and revision.key == "db_pool_size"
            and revision.value == 20
        ][0]
        self.assertIsNotNone(prior.valid_to)

        restart_result = self.conductor.apply_action(
            incident.incident_id,
            "restart_service",
            "checkout-svc",
        )
        self.assertTrue(restart_result.resolved)
        self.assertEqual(240, incident.mttr_seconds)
        self.assertEqual(1, self.conductor.state.services["checkout-svc"].restart_count)

    def test_cache_failover_repoints_dependency_and_resolves(self) -> None:
        first = self.conductor.inject_next()
        self.conductor.apply_action(
            first.incident_id,
            "rollback_deploy",
            "checkout-svc",
            {"target_version": "1.4.2"},
        )
        second = self.conductor.inject_next()
        self.conductor.apply_action(
            second.incident_id,
            "set_config",
            "checkout-svc",
            {"key": "db_pool_size", "value": 80},
        )
        self.conductor.apply_action(
            second.incident_id,
            "restart_service",
            "checkout-svc",
        )
        incident = self.conductor.inject_next()

        result = self.conductor.apply_action(
            incident.incident_id,
            "failover_dependency",
            "checkout-svc",
            {
                "dependency_key": "checkout-cache",
                "to_service": "redis-cluster-v2-replica",
            },
        )

        self.assertTrue(result.resolved)
        self.assertEqual(
            "redis-cluster-v2-replica",
            self.conductor.state.dependencies["checkout-cache"].to_service,
        )
        self.assertEqual(Health.HEALTHY, self.conductor.state.services["checkout-svc"].health)

    def test_resolved_incident_rejects_duplicate_actions(self) -> None:
        incident = self.conductor.inject_next()
        self.conductor.apply_action(
            incident.incident_id,
            "rollback_deploy",
            "checkout-svc",
            {"target_version": "1.4.2"},
        )
        with self.assertRaisesRegex(SimulationError, "already resolved"):
            self.conductor.apply_action(
                incident.incident_id,
                "restart_service",
                "checkout-svc",
            )

    def test_catalog_meets_occurrence_invariant(self) -> None:
        fixture = json.loads(
            (SIMULATOR_ROOT / "fixtures" / "scenarios.json").read_text()
        )
        counts: dict[str, int] = {}
        for scenario in fixture["scenarios"]:
            counts[scenario["family_id"]] = counts.get(scenario["family_id"], 0) + 1
        self.assertEqual(10, len(counts))
        self.assertEqual(2, counts["F10_NOVEL"])
        self.assertTrue(
            all(
                count >= 3
                for family, count in counts.items()
                if family != "F10_NOVEL"
            ),
            counts,
        )

    def test_pool_near_miss_rejects_pool_remediation(self) -> None:
        fixture = json.loads(
            (SIMULATOR_ROOT / "fixtures" / "scenarios.json").read_text()
        )
        oracle = json.loads(
            (SIMULATOR_ROOT / "fixtures" / "oracles.json").read_text()
        )
        isolated = {
            **fixture,
            "scenarios": [
                scenario
                for scenario in fixture["scenarios"]
                if scenario["scenario_id"] == "f2-near-miss-slow-query"
            ],
        }
        conductor = Conductor(isolated, oracle)
        incident = conductor.inject_next()

        wrong = conductor.apply_action(
            incident.incident_id,
            "set_config",
            "checkout-svc",
            {"key": "db_pool_size", "value": 80},
        )
        self.assertFalse(wrong.resolved)
        self.assertEqual("no_effect", wrong.outcome)

        safe = conductor.apply_action(
            incident.incident_id,
            "no_op_page_human",
            "checkout-svc",
        )
        self.assertTrue(safe.resolved)

    def test_all_phase2_family_oracles_close_the_loop(self) -> None:
        plans = {
            "F4_TRAFFIC_SATURATION": [
                ("scale_service", "checkout-svc", {"capacity_units": 8}),
                ("throttle_traffic", "checkout-svc", {"traffic_percent": 75}),
            ],
            "F5_RETRY_STORM": [
                ("throttle_traffic", "checkout-svc", {"traffic_percent": 60})
            ],
            "F6_MEMORY_LEAK": [
                ("restart_service", "fraud-svc", {}),
            ],
            "F7_CONFIG_REGRESSION": [
                (
                    "set_config",
                    "checkout-svc",
                    {"key": "express_checkout_enabled", "value": False},
                )
            ],
            "F8_DATASTORE_FAILOVER": [
                (
                    "failover_dependency",
                    "checkout-svc",
                    {
                        "dependency_key": "checkout-orders",
                        "to_service": "orders-db-replica",
                    },
                )
            ],
            "F9_PAYMENT_PROVIDER_OUTAGE": [
                (
                    "failover_dependency",
                    "payment-svc",
                    {
                        "dependency_key": "payment-provider",
                        "to_service": "payment-provider-secondary",
                    },
                ),
                (
                    "throttle_traffic",
                    "payment-svc",
                    {"traffic_percent": 50},
                ),
            ],
            "F10_NOVEL": [
                ("no_op_page_human", "checkout-svc", {}),
            ],
        }
        fixture_path = SIMULATOR_ROOT / "fixtures" / "scenarios.json"
        fixture = json.loads(fixture_path.read_text())
        oracle = json.loads(
            (SIMULATOR_ROOT / "fixtures" / "oracles.json").read_text()
        )

        for family_id, actions in plans.items():
            with self.subTest(family_id=family_id):
                isolated = dict(fixture)
                isolated["scenarios"] = [
                    scenario
                    for scenario in fixture["scenarios"]
                    if scenario["family_id"] == family_id
                ][:1]
                conductor = Conductor(isolated, oracle)
                incident = conductor.inject_next()
                result = None
                for action_type, target, params in actions:
                    result = conductor.apply_action(
                        incident.incident_id,
                        action_type,
                        target,
                        params,
                    )
                self.assertIsNotNone(result)
                self.assertTrue(result.resolved)
                self.assertEqual(IncidentStatus.RESOLVED, incident.status)

        saturation = Conductor(
            {
                **fixture,
                "scenarios": [
                    scenario
                    for scenario in fixture["scenarios"]
                    if scenario["family_id"] == "F4_TRAFFIC_SATURATION"
                ][:1],
            },
            oracle,
        )
        incident = saturation.inject_next()
        saturation.apply_action(
            incident.incident_id,
            "scale_service",
            "checkout-svc",
            {"capacity_units": 8},
        )
        saturation.apply_action(
            incident.incident_id,
            "throttle_traffic",
            "checkout-svc",
            {"traffic_percent": 75},
        )
        self.assertEqual(
            75,
            saturation.state.current_config(
                "checkout-svc", "traffic_limit_percent"
            ).value,
        )


if __name__ == "__main__":
    unittest.main()
