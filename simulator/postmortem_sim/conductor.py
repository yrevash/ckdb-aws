from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from .models import (
    Alert,
    ConfigRevision,
    Dependency,
    Deployment,
    Health,
    Incident,
    IncidentObservation,
    IncidentStatus,
    Order,
    RemediationAction,
    SLO,
    SLOStatus,
    Service,
    WorldState,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
ID_NAMESPACE = UUID("7d4de529-f859-4c57-b743-e72c94f457cc")


class SimulationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    applied: bool
    resolved: bool
    outcome: str
    incident_status: IncidentStatus


class Conductor:
    """Seeded, deterministic incident conductor and resolution oracle."""

    def __init__(
        self,
        fixture: dict[str, Any],
        oracle: dict[str, Any],
    ) -> None:
        self.seed = int(fixture["seed"])
        self.start_time = self._parse_time(fixture["start_time"])
        self._scenarios = sorted(
            fixture["scenarios"],
            key=lambda item: (item["inject_at_seconds"], item["scenario_id"]),
        )
        self._oracle = oracle["families"]
        self._scenario_oracle = oracle.get("scenarios", {})
        self._next_scenario = 0
        self.state = WorldState(now=self.start_time)
        self._load_world(fixture)

    @classmethod
    def from_files(
        cls,
        scenario_path: Path | str = FIXTURE_ROOT / "scenarios.json",
        oracle_path: Path | str = FIXTURE_ROOT / "oracles.json",
    ) -> "Conductor":
        return cls(
            json.loads(Path(scenario_path).read_text()),
            json.loads(Path(oracle_path).read_text()),
        )

    @property
    def remaining_scenarios(self) -> int:
        return len(self._scenarios) - self._next_scenario

    def advance_time(self, seconds: int) -> None:
        """Advance deterministic diagnostic/reasoning time."""

        if seconds < 0:
            raise SimulationError("simulation time cannot move backwards")
        self.state.now += timedelta(seconds=seconds)

    def inject_next(self) -> Incident:
        if self._next_scenario >= len(self._scenarios):
            raise SimulationError("no scheduled scenarios remain")

        scenario = self._scenarios[self._next_scenario]
        self._next_scenario += 1
        scheduled_at = self.start_time + timedelta(
            seconds=int(scenario["inject_at_seconds"])
        )
        self.state.now = max(self.state.now, scheduled_at)

        family = scenario["family_id"]
        if family == "F1_BAD_DEPLOY":
            return self._inject_bad_deploy(scenario)
        if family == "F2_POOL_EXHAUSTION":
            return self._inject_pool_exhaustion(scenario)
        if family == "F3_CACHE_OUTAGE":
            return self._inject_cache_outage(scenario)
        if family == "F4_TRAFFIC_SATURATION":
            return self._inject_traffic_saturation(scenario)
        if family == "F5_RETRY_STORM":
            return self._inject_retry_storm(scenario)
        if family == "F6_MEMORY_LEAK":
            return self._inject_memory_leak(scenario)
        if family == "F7_CONFIG_REGRESSION":
            return self._inject_config_regression(scenario)
        if family == "F8_DATASTORE_FAILOVER":
            return self._inject_datastore_failover(scenario)
        if family == "F9_PAYMENT_PROVIDER_OUTAGE":
            return self._inject_payment_provider_outage(scenario)
        if family == "F10_NOVEL":
            return self._inject_novel(scenario)
        if family == "F11_POOL_DRIVER_MIGRATION":
            return self._inject_pool_driver_migration(scenario)
        if family == "F12_CACHE_TOPOLOGY_MIGRATION":
            return self._inject_cache_topology_migration(scenario)
        raise SimulationError(f"unsupported incident family: {family}")

    def observe_incident(self, incident_id: str) -> IncidentObservation:
        """Build the responder's view without exposing family/oracle labels."""

        incident = self.state.incidents.get(incident_id)
        if incident is None:
            raise SimulationError(f"unknown incident: {incident_id}")
        alert = next(
            (
                candidate
                for candidate in self.state.alerts.values()
                if candidate.incident_id == incident_id and candidate.cleared_at is None
            ),
            None,
        )
        if alert is None:
            raise SimulationError(f"incident has no active alert: {incident_id}")
        slo = self.state.slos[(alert.service, alert.slo_kind)]
        service = self.state.services[incident.service]
        return IncidentObservation(
            incident_id=incident.incident_id,
            title=incident.title,
            service=incident.service,
            severity=incident.severity,
            signal=alert.signal,
            slo_kind=alert.slo_kind,
            slo_current_value=slo.current_value,
            slo_threshold=slo.threshold,
            current_version=service.current_version,
            previous_stable_version=service.previous_stable_version,
            observed_at=self.state.now,
        )

    def apply_action(
        self,
        incident_id: str,
        action_type: str,
        target_service: str,
        params: dict[str, Any] | None = None,
        *,
        applied_by: str = "agent:postmortem",
        memory_ref: str | None = None,
    ) -> ActionResult:
        params = dict(params or {})
        incident = self.state.incidents.get(incident_id)
        if incident is None:
            raise SimulationError(f"unknown incident: {incident_id}")
        if incident.status is IncidentStatus.RESOLVED:
            raise SimulationError(f"incident is already resolved: {incident_id}")
        if target_service not in self.state.services:
            raise SimulationError(f"unknown target service: {target_service}")

        policy = self._oracle_for(incident)
        required = policy["required_actions"]
        expected = required[incident.oracle_step] if incident.oracle_step < len(required) else None
        is_expected = expected is not None and self._matches(
            expected, incident, action_type, target_service, params
        )

        self._mutate_action(action_type, target_service, params)
        self.state.now += timedelta(
            seconds=int(
                policy[
                    "action_seconds" if is_expected else "wrong_action_penalty_seconds"
                ]
            )
        )

        if is_expected:
            incident.oracle_step += 1
            incident.status = IncidentStatus.MITIGATING
            outcome = "success"
        else:
            outcome = "no_effect"
            self._add_failed_orders(incident, count=2, error_code="MITIGATION_INEFFECTIVE")

        action = RemediationAction(
            action_id=self._id(
                "action", f"{incident.scenario_id}:{len(self.state.actions)}"
            ),
            incident_id=incident_id,
            action_type=action_type,
            target_service=target_service,
            params=params,
            applied_at=self.state.now,
            applied_by=applied_by,
            outcome=outcome,
            memory_ref=memory_ref,
        )
        self.state.actions.append(action)

        resolved = incident.oracle_step == len(required)
        if resolved:
            self._resolve(incident)

        return ActionResult(
            action_id=action.action_id,
            applied=True,
            resolved=resolved,
            outcome=outcome,
            incident_status=incident.status,
        )

    def _load_world(self, fixture: dict[str, Any]) -> None:
        for item in fixture["services"]:
            self.state.services[item["name"]] = Service(
                service_id=self._id("service", item["name"]),
                name=item["name"],
                tier=item["tier"],
                owner_team=item["owner_team"],
                health=Health.HEALTHY,
                current_version=item["current_version"],
                previous_stable_version=item.get("previous_stable_version"),
                capacity_units=int(item.get("capacity_units", 1)),
            )

        for item in fixture["dependencies"]:
            self.state.dependencies[item["dependency_key"]] = Dependency(
                dependency_id=self._id("dependency", item["dependency_key"]),
                dependency_key=item["dependency_key"],
                from_service=item["from_service"],
                to_service=item["to_service"],
                dependency_type=item["dependency_type"],
                criticality=item["criticality"],
            )

        for item in fixture["slos"]:
            self.state.slos[(item["service"], item["kind"])] = SLO(
                slo_id=self._id("slo", f"{item['service']}:{item['kind']}"),
                service=item["service"],
                kind=item["kind"],
                threshold=float(item["threshold"]),
                window_seconds=int(item["window_seconds"]),
                current_value=float(item["baseline"]),
            )

        for item in fixture["configs"]:
            self.state.configs.append(
                ConfigRevision(
                    config_id=self._id("config", f"{item['service']}:{item['key']}:0"),
                    service=item["service"],
                    key=item["key"],
                    value=item["value"],
                    valid_from=self.start_time,
                    valid_to=None,
                    source="fixture",
                )
            )

        for service in self.state.services.values():
            self.state.deployments.append(
                Deployment(
                    deploy_id=self._id("deploy", f"{service.name}:initial"),
                    service=service.name,
                    version=service.current_version,
                    action="deploy",
                    deployed_at=self.start_time,
                )
            )

    def _inject_bad_deploy(self, scenario: dict[str, Any]) -> Incident:
        service = self.state.services[scenario["target_service"]]
        service.previous_stable_version = service.current_version
        service.current_version = scenario["fault"]["bad_version"]
        service.health = Health.DEGRADED
        self.state.deployments.append(
            Deployment(
                deploy_id=self._id("deploy", f"{scenario['scenario_id']}:bad"),
                service=service.name,
                version=service.current_version,
                action="deploy",
                deployed_at=self.state.now,
            )
        )
        return self._open_incident(
            scenario,
            slo_kind="error_rate",
            current_value=float(scenario["fault"]["error_rate"]),
            signal=f"HTTP 5xx rose after deploy {service.current_version}",
            error_code="CHECKOUT_5XX",
        )

    def _inject_pool_exhaustion(self, scenario: dict[str, Any]) -> Incident:
        service = self.state.services[scenario["target_service"]]
        service.health = Health.DEGRADED
        if scenario["fault"].get("near_miss", False):
            return self._open_incident(
                scenario,
                slo_kind="latency",
                current_value=float(scenario["fault"]["p99_ms"]),
                signal=(
                    "slow SQL query plan causes long scans; pool health confirmed "
                    "and resource gauges remain normal"
                ),
                error_code="SLOW_QUERY_PLAN",
            )
        current = self.state.current_config(service.name, "db_pool_size")
        if current is None:
            raise SimulationError(f"db_pool_size missing for {service.name}")
        return self._open_incident(
            scenario,
            slo_kind="latency",
            current_value=float(scenario["fault"]["p99_ms"]),
            signal="database too many connections; p99 latency breached",
            error_code="DB_POOL_EXHAUSTED",
        )

    def _inject_cache_outage(self, scenario: dict[str, Any]) -> Incident:
        dependency_key = scenario["fault"]["dependency_key"]
        dependency = self.state.dependencies[dependency_key]
        dependency.to_service = scenario["fault"]["failed_service"]
        dependency.status = "degraded"
        self.state.services[dependency.to_service].health = Health.DOWN
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="availability",
            current_value=float(scenario["fault"]["availability"]),
            signal=f"cache timeout cascade through {dependency.to_service}",
            error_code="CACHE_TIMEOUT",
        )

    def _inject_traffic_saturation(self, scenario: dict[str, Any]) -> Incident:
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="saturation",
            current_value=float(scenario["fault"]["utilization"]),
            signal="traffic spike saturated CPU; checkout latency rising",
            error_code="SERVICE_SATURATED",
        )

    def _inject_retry_storm(self, scenario: dict[str, Any]) -> Incident:
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="latency",
            current_value=float(scenario["fault"]["p99_ms"]),
            signal="retry storm amplified request rate after an upstream blip",
            error_code="RETRY_STORM",
        )

    def _inject_memory_leak(self, scenario: dict[str, Any]) -> Incident:
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="saturation",
            current_value=float(scenario["fault"]["memory_utilization"]),
            signal="gradual memory leak reached OOM pressure and process crashes",
            error_code="MEMORY_PRESSURE",
        )

    def _inject_config_regression(self, scenario: dict[str, Any]) -> Incident:
        service_name = scenario["target_service"]
        self._set_config(
            service_name,
            scenario["fault"]["key"],
            scenario["fault"]["bad_value"],
            source="fault",
        )
        self.state.services[service_name].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="error_rate",
            current_value=float(scenario["fault"]["error_rate"]),
            signal=f"error spike followed config flag {scenario['fault']['key']} change",
            error_code="CONFIG_REGRESSION",
        )

    def _inject_datastore_failover(self, scenario: dict[str, Any]) -> Incident:
        dependency = self.state.dependencies[scenario["fault"]["dependency_key"]]
        dependency.to_service = scenario["fault"]["failed_service"]
        dependency.status = "degraded"
        self.state.services[dependency.to_service].health = Health.DOWN
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="availability",
            current_value=float(scenario["fault"]["availability"]),
            signal="orders datastore primary unavailable; checkout writes failing",
            error_code="DATASTORE_UNAVAILABLE",
        )

    def _inject_payment_provider_outage(
        self, scenario: dict[str, Any]
    ) -> Incident:
        dependency = self.state.dependencies[scenario["fault"]["dependency_key"]]
        dependency.to_service = scenario["fault"]["failed_service"]
        dependency.status = "degraded"
        self.state.services[dependency.to_service].health = Health.DOWN
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="availability",
            current_value=float(scenario["fault"]["availability"]),
            signal="upstream payment provider auth failures are blocking checkout",
            error_code="PAYMENT_PROVIDER_DOWN",
        )

    def _inject_novel(self, scenario: dict[str, Any]) -> Incident:
        self.state.services[scenario["target_service"]].health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="latency",
            current_value=float(scenario["fault"]["p99_ms"]),
            signal="ambiguous clock-skew signature with no known causal pattern",
            error_code="UNKNOWN_FAILURE",
        )

    def _inject_pool_driver_migration(self, scenario: dict[str, Any]) -> Incident:
        """Temporal-drift family: a once-correct pool-exhaustion fix
        (raise db_pool_size, then restart) stops working once the platform
        migrates the service off the legacy thread-pool driver onto a
        multiplexed one. The incident looks identical (same signal, same
        error code) before and after; only the environment fact
        `pool_driver` -- itself a bitemporal config transition via
        `_set_config` -- has changed, so the *correct* remediation depends on
        which fact was valid at the incident's decision time, not just on
        the alert text.
        """

        service = self.state.services[scenario["target_service"]]
        service.health = Health.DEGRADED
        if scenario["fault"].get("environment_migrated", False):
            self._set_config(
                service.name, "pool_driver", "multiplexed", source="platform_migration"
            )
        return self._open_incident(
            scenario,
            slo_kind="latency",
            current_value=float(scenario["fault"]["p99_ms"]),
            signal="database too many connections; p99 latency breached",
            error_code="DB_POOL_EXHAUSTED",
        )

    def _inject_cache_topology_migration(self, scenario: dict[str, Any]) -> Incident:
        """Temporal-drift family: a once-correct cache failover target
        (fail over to the on-prem redis replica) stops working once the
        platform migrates cache topology to a managed cache service and
        decommissions the on-prem replica. Same cascading-cache signal
        before and after; the fact that has changed is which failover target
        is currently sanctioned (`cache_failover_target`), tracked the same
        bitemporal way as any other config transition.
        """

        dependency_key = scenario["fault"]["dependency_key"]
        dependency = self.state.dependencies[dependency_key]
        service = self.state.services[scenario["target_service"]]
        if scenario["fault"].get("environment_migrated", False):
            self._set_config(
                service.name,
                "cache_failover_target",
                "cache-managed-primary",
                source="platform_migration",
            )
            self.state.services["cache-onprem-replica"].health = Health.DOWN
        dependency.to_service = scenario["fault"]["failed_service"]
        dependency.status = "degraded"
        self.state.services[dependency.to_service].health = Health.DOWN
        service.health = Health.DEGRADED
        return self._open_incident(
            scenario,
            slo_kind="availability",
            current_value=float(scenario["fault"]["availability"]),
            signal=f"cache timeout cascade through {dependency.to_service}",
            error_code="CACHE_TIMEOUT",
        )

    def _open_incident(
        self,
        scenario: dict[str, Any],
        *,
        slo_kind: str,
        current_value: float,
        signal: str,
        error_code: str,
    ) -> Incident:
        incident_id = self._id("incident", scenario["scenario_id"])
        incident = Incident(
            incident_id=incident_id,
            scenario_id=scenario["scenario_id"],
            title=scenario["title"],
            service=scenario["target_service"],
            severity=scenario["severity"],
            family_id=scenario["family_id"],
            variant_id=scenario["variant_id"],
            status=IncidentStatus.OPEN,
            opened_at=self.state.now,
        )
        self.state.incidents[incident_id] = incident

        slo = self.state.slos[(incident.service, slo_kind)]
        slo.current_value = current_value
        slo.burn_rate = float(scenario["fault"].get("burn_rate", 8.0))
        slo.status = SLOStatus.BREACHING

        alert = Alert(
            alert_id=self._id("alert", scenario["scenario_id"]),
            incident_id=incident_id,
            service=incident.service,
            slo_kind=slo_kind,
            signal=signal,
            fired_at=self.state.now,
        )
        self.state.alerts[alert.alert_id] = alert
        self._add_failed_orders(
            incident,
            count=int(scenario.get("failed_orders", 5)),
            error_code=error_code,
        )
        return incident

    def _mutate_action(
        self, action_type: str, target_service: str, params: dict[str, Any]
    ) -> None:
        service = self.state.services[target_service]
        if action_type == "rollback_deploy":
            target_version = params.get("target_version")
            if not isinstance(target_version, str):
                raise SimulationError("rollback_deploy requires target_version")
            for deployment in reversed(self.state.deployments):
                if deployment.service == target_service and deployment.version == service.current_version:
                    deployment.rolled_back = True
                    break
            service.current_version = target_version
            self.state.deployments.append(
                Deployment(
                    deploy_id=self._id(
                        "deploy", f"rollback:{target_service}:{len(self.state.deployments)}"
                    ),
                    service=target_service,
                    version=target_version,
                    action="rollback",
                    deployed_at=self.state.now,
                )
            )
        elif action_type == "set_config":
            key = params.get("key")
            if not isinstance(key, str) or "value" not in params:
                raise SimulationError("set_config requires key and value")
            self._set_config(target_service, key, params["value"], source="remediation")
        elif action_type == "restart_service":
            service.restart_count += 1
        elif action_type == "failover_dependency":
            key = params.get("dependency_key")
            destination = params.get("to_service")
            if key not in self.state.dependencies:
                raise SimulationError("failover_dependency requires a known dependency_key")
            if destination not in self.state.services:
                raise SimulationError("failover_dependency requires a known to_service")
            dependency = self.state.dependencies[key]
            dependency.to_service = destination
            dependency.status = "active"
        elif action_type == "scale_service":
            units = params.get("capacity_units")
            if not isinstance(units, int) or units <= 0:
                raise SimulationError("scale_service requires positive capacity_units")
            service.capacity_units = units
        elif action_type == "throttle_traffic":
            percent = params.get("traffic_percent")
            if not isinstance(percent, int) or not 1 <= percent <= 100:
                raise SimulationError(
                    "throttle_traffic requires traffic_percent between 1 and 100"
                )
            self._set_config(
                target_service,
                "traffic_limit_percent",
                percent,
                source="remediation",
            )
        elif action_type in {"no_op_page_human", "escalate"}:
            return
        else:
            raise SimulationError(f"unsupported remediation action: {action_type}")

    def _set_config(
        self, service: str, key: str, value: Any, *, source: str
    ) -> None:
        old = self.state.current_config(service, key)
        if old is not None:
            old.valid_to = self.state.now
        self.state.configs.append(
            ConfigRevision(
                config_id=self._id(
                    "config",
                    f"{service}:{key}:{len(self.state.configs)}",
                ),
                service=service,
                key=key,
                value=value,
                valid_from=self.state.now,
                valid_to=None,
                source=source,
            )
        )

    def _matches(
        self,
        expected: dict[str, Any],
        incident: Incident,
        action_type: str,
        target_service: str,
        params: dict[str, Any],
    ) -> bool:
        expected_target = (
            incident.service
            if expected["target_service"] == "$incident.service"
            else expected["target_service"]
        )
        if expected["action_type"] != action_type or expected_target != target_service:
            return False

        service = self.state.services[incident.service]
        for key, expected_value in expected.get("params", {}).items():
            if expected_value == "$incident.previous_stable_version":
                expected_value = service.previous_stable_version
            if params.get(key) != expected_value:
                return False
        return True

    def _resolve(self, incident: Incident) -> None:
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = self.state.now
        incident.mttr_seconds = int((self.state.now - incident.opened_at).total_seconds())
        self.state.services[incident.service].health = Health.HEALTHY

        for alert in self.state.alerts.values():
            if alert.incident_id == incident.incident_id and alert.cleared_at is None:
                alert.cleared_at = self.state.now
                slo = self.state.slos[(alert.service, alert.slo_kind)]
                slo.status = SLOStatus.HEALTHY
                slo.burn_rate = 0.0
                if alert.slo_kind == "availability":
                    slo.current_value = 0.9995
                else:
                    slo.current_value = 0.0

    def _add_failed_orders(
        self, incident: Incident, *, count: int, error_code: str
    ) -> None:
        for index in range(count):
            self.state.orders.append(
                Order(
                    order_id=self._id(
                        "order",
                        f"{incident.scenario_id}:{len(self.state.orders)}:{index}",
                    ),
                    incident_id=incident.incident_id,
                    status="failed",
                    amount_cents=1500 + ((self.seed + index * 7919) % 8500),
                    error_code=error_code,
                    created_at=self.state.now + timedelta(seconds=index),
                )
            )

    def _id(self, kind: str, key: str) -> str:
        return str(uuid5(ID_NAMESPACE, f"{self.seed}:{kind}:{key}"))

    def _oracle_for(self, incident: Incident) -> dict[str, Any]:
        return self._scenario_oracle.get(
            incident.scenario_id,
            self._oracle[incident.family_id],
        )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
