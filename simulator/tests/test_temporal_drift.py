"""Phase 3 / Track B: temporal-drift family tests.

Two drift families (F11_POOL_DRIVER_MIGRATION, F12_CACHE_TOPOLOGY_MIGRATION)
live in isolated fixtures (simulator/fixtures/drift_scenarios.json,
drift_oracles.json) so this suite never touches the Phase 2 default
scenario/oracle fixtures or their value-locked invariants
(test_catalog_meets_occurrence_invariant, "exactly 10 families", ...).
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


SIMULATOR_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIMULATOR_ROOT))

from postmortem_sim import Conductor, Health, IncidentStatus, SimulationError  # noqa: E402


DRIFT_SCENARIO_PATH = SIMULATOR_ROOT / "fixtures" / "drift_scenarios.json"
DRIFT_ORACLE_PATH = SIMULATOR_ROOT / "fixtures" / "drift_oracles.json"


def _drift_conductor() -> Conductor:
    return Conductor(
        json.loads(DRIFT_SCENARIO_PATH.read_text()),
        json.loads(DRIFT_ORACLE_PATH.read_text()),
    )


class TemporalDriftTests(unittest.TestCase):
    def test_default_catalog_is_untouched_by_drift_fixtures(self) -> None:
        """Adding drift families must be purely additive: the default
        scenarios.json/oracles.json Phase 2 uses are not edited, so the
        Phase-2-locked "exactly 10 families" invariant still holds there.
        """

        default = json.loads((SIMULATOR_ROOT / "fixtures" / "scenarios.json").read_text())
        families = {scenario["family_id"] for scenario in default["scenarios"]}
        self.assertEqual(10, len(families))
        self.assertNotIn("F11_POOL_DRIVER_MIGRATION", families)
        self.assertNotIn("F12_CACHE_TOPOLOGY_MIGRATION", families)
        # Drift families exist only in their own isolated catalog.
        drift = json.loads(DRIFT_SCENARIO_PATH.read_text())
        drift_families = {scenario["family_id"] for scenario in drift["scenarios"]}
        self.assertEqual(
            {"F11_POOL_DRIVER_MIGRATION", "F12_CACHE_TOPOLOGY_MIGRATION"}, drift_families
        )

    def test_observation_carries_decision_time_without_leaking_oracle_labels(self) -> None:
        conductor = _drift_conductor()
        incident = conductor.inject_next()
        observation = conductor.observe_incident(incident.incident_id)

        self.assertEqual(conductor.state.now, observation.observed_at)
        self.assertFalse(hasattr(observation, "family_id"))
        self.assertFalse(hasattr(observation, "variant_id"))
        self.assertFalse(hasattr(observation, "required_actions"))

    def test_f11_old_fix_resolves_before_the_pool_driver_migration(self) -> None:
        conductor = _drift_conductor()
        incident = conductor.inject_next()
        self.assertEqual("legacy-driver", incident.variant_id)

        result = conductor.apply_action(
            incident.incident_id,
            "set_config",
            "risk-svc",
            {"key": "db_pool_size", "value": 80},
        )
        self.assertFalse(result.resolved)
        self.assertEqual("success", result.outcome)

        result = conductor.apply_action(incident.incident_id, "restart_service", "risk-svc")
        self.assertTrue(result.resolved)
        self.assertEqual(IncidentStatus.RESOLVED, incident.status)
        self.assertEqual(Health.HEALTHY, conductor.state.services["risk-svc"].health)

    def test_f11_old_fix_becomes_wrong_after_the_pool_driver_migration(self) -> None:
        """The same once-correct fix (raise db_pool_size, restart) must NOT
        resolve the recurrence once the environment has drifted -- this is
        the load-bearing assertion for "a once-correct fix becomes wrong".
        """

        conductor = _drift_conductor()
        first = conductor.inject_next()
        conductor.apply_action(
            first.incident_id, "set_config", "risk-svc", {"key": "db_pool_size", "value": 80}
        )
        conductor.apply_action(first.incident_id, "restart_service", "risk-svc")
        self.assertEqual(IncidentStatus.RESOLVED, first.status)

        second = conductor.inject_next()
        self.assertEqual("multiplexed-driver", second.variant_id)
        self.assertEqual(
            "multiplexed",
            conductor.state.current_config("risk-svc", "pool_driver").value,
        )

        stale_fix = conductor.apply_action(
            second.incident_id,
            "set_config",
            "risk-svc",
            {"key": "db_pool_size", "value": 80},
        )
        self.assertFalse(stale_fix.resolved)
        self.assertEqual("no_effect", stale_fix.outcome)
        self.assertEqual(IncidentStatus.OPEN, second.status)

        stale_fix_restart = conductor.apply_action(
            second.incident_id, "restart_service", "risk-svc"
        )
        self.assertFalse(stale_fix_restart.resolved)

        corrected_fix = conductor.apply_action(
            second.incident_id,
            "set_config",
            "risk-svc",
            {"key": "pool_multiplexing_enabled", "value": True},
        )
        self.assertTrue(corrected_fix.resolved)
        self.assertEqual(IncidentStatus.RESOLVED, second.status)

    def _drain_f11_incidents(self, conductor: Conductor) -> None:
        """Resolve both F11 incidents with their (respectively correct)
        fixes so the stream can reach the F12 scenarios undisturbed.
        """

        first = conductor.inject_next()
        conductor.apply_action(
            first.incident_id, "set_config", "risk-svc", {"key": "db_pool_size", "value": 80}
        )
        conductor.apply_action(first.incident_id, "restart_service", "risk-svc")
        second = conductor.inject_next()
        conductor.apply_action(
            second.incident_id,
            "set_config",
            "risk-svc",
            {"key": "pool_multiplexing_enabled", "value": True},
        )

    def test_f12_old_failover_target_resolves_before_the_cache_migration(self) -> None:
        conductor = _drift_conductor()
        self._drain_f11_incidents(conductor)

        third = conductor.inject_next()
        self.assertEqual("onprem-topology", third.variant_id)
        result = conductor.apply_action(
            third.incident_id,
            "failover_dependency",
            "storefront-svc",
            {"dependency_key": "storefront-cache", "to_service": "cache-onprem-replica"},
        )
        self.assertTrue(result.resolved)
        self.assertEqual(
            "cache-onprem-replica",
            conductor.state.dependencies["storefront-cache"].to_service,
        )

    def test_f12_old_failover_target_becomes_wrong_after_the_cache_migration(self) -> None:
        conductor = _drift_conductor()
        self._drain_f11_incidents(conductor)
        conductor.inject_next()  # f12-cache-onprem, deliberately left unresolved

        fourth = conductor.inject_next()
        self.assertEqual("managed-topology", fourth.variant_id)
        self.assertEqual(
            "cache-managed-primary",
            conductor.state.current_config(
                "storefront-svc", "cache_failover_target"
            ).value,
        )

        stale_fix = conductor.apply_action(
            fourth.incident_id,
            "failover_dependency",
            "storefront-svc",
            {"dependency_key": "storefront-cache", "to_service": "cache-onprem-replica"},
        )
        self.assertFalse(stale_fix.resolved)
        self.assertEqual("no_effect", stale_fix.outcome)

        corrected_fix = conductor.apply_action(
            fourth.incident_id,
            "failover_dependency",
            "storefront-svc",
            {"dependency_key": "storefront-cache", "to_service": "cache-managed-primary"},
        )
        self.assertTrue(corrected_fix.resolved)
        self.assertEqual(
            "cache-managed-primary",
            conductor.state.dependencies["storefront-cache"].to_service,
        )

    def test_unsupported_family_still_raises(self) -> None:
        conductor = _drift_conductor()
        conductor._scenarios.append(  # noqa: SLF001 -- deliberately probing dispatch
            {
                "scenario_id": "unsupported",
                "family_id": "F999_DOES_NOT_EXIST",
                "variant_id": "n/a",
                "title": "n/a",
                "severity": "SEV3",
                "target_service": "risk-svc",
                "inject_at_seconds": 999999,
                "failed_orders": 0,
                "fault": {},
            }
        )
        for _ in range(4):
            conductor.inject_next()
        with self.assertRaisesRegex(SimulationError, "unsupported incident family"):
            conductor.inject_next()


if __name__ == "__main__":
    unittest.main()
