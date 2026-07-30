from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Health(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    RECOVERING = "recovering"


class IncidentStatus(StrEnum):
    OPEN = "open"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"


class SLOStatus(StrEnum):
    HEALTHY = "healthy"
    BREACHING = "breaching"


@dataclass
class Service:
    service_id: str
    name: str
    tier: str
    owner_team: str
    health: Health
    current_version: str
    previous_stable_version: str | None
    capacity_units: int
    restart_count: int = 0


@dataclass
class Dependency:
    dependency_id: str
    dependency_key: str
    from_service: str
    to_service: str
    dependency_type: str
    criticality: str
    status: str = "active"


@dataclass
class SLO:
    slo_id: str
    service: str
    kind: str
    threshold: float
    window_seconds: int
    current_value: float
    burn_rate: float = 0.0
    status: SLOStatus = SLOStatus.HEALTHY


@dataclass
class ConfigRevision:
    config_id: str
    service: str
    key: str
    value: Any
    valid_from: datetime
    valid_to: datetime | None
    source: str


@dataclass
class Deployment:
    deploy_id: str
    service: str
    version: str
    action: str
    deployed_at: datetime
    rolled_back: bool = False


@dataclass
class Alert:
    alert_id: str
    incident_id: str
    service: str
    slo_kind: str
    signal: str
    fired_at: datetime
    cleared_at: datetime | None = None


@dataclass
class Incident:
    incident_id: str
    scenario_id: str
    title: str
    service: str
    severity: str
    family_id: str
    variant_id: str
    status: IncidentStatus
    opened_at: datetime
    resolved_at: datetime | None = None
    mttr_seconds: int | None = None
    oracle_step: int = 0


@dataclass(frozen=True)
class IncidentObservation:
    """Agent-visible incident state; deliberately excludes ground-truth labels."""

    incident_id: str
    title: str
    service: str
    severity: str
    signal: str
    slo_kind: str
    slo_current_value: float
    slo_threshold: float
    current_version: str
    previous_stable_version: str | None


@dataclass
class Order:
    order_id: str
    incident_id: str
    status: str
    amount_cents: int
    error_code: str | None
    created_at: datetime


@dataclass
class RemediationAction:
    action_id: str
    incident_id: str
    action_type: str
    target_service: str
    params: dict[str, Any]
    applied_at: datetime
    applied_by: str
    outcome: str
    memory_ref: str | None


@dataclass
class WorldState:
    now: datetime
    services: dict[str, Service] = field(default_factory=dict)
    dependencies: dict[str, Dependency] = field(default_factory=dict)
    slos: dict[tuple[str, str], SLO] = field(default_factory=dict)
    configs: list[ConfigRevision] = field(default_factory=list)
    deployments: list[Deployment] = field(default_factory=list)
    alerts: dict[str, Alert] = field(default_factory=dict)
    incidents: dict[str, Incident] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    actions: list[RemediationAction] = field(default_factory=list)

    def current_config(self, service: str, key: str) -> ConfigRevision | None:
        for revision in reversed(self.configs):
            if (
                revision.service == service
                and revision.key == key
                and revision.valid_to is None
            ):
                return revision
        return None

    def snapshot(self) -> dict[str, Any]:
        """Return a stable, JSON-compatible state representation."""

        def normalize(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).isoformat()
            if isinstance(value, StrEnum):
                return str(value)
            if isinstance(value, dict):
                return {
                    str(key): normalize(item)
                    for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
                }
            if isinstance(value, (list, tuple)):
                return [normalize(item) for item in value]
            return value

        return normalize(
            {
                "now": self.now,
                "services": {
                    name: asdict(service)
                    for name, service in sorted(self.services.items())
                },
                "dependencies": {
                    key: asdict(dependency)
                    for key, dependency in sorted(self.dependencies.items())
                },
                "slos": {
                    f"{service}:{kind}": asdict(slo)
                    for (service, kind), slo in sorted(self.slos.items())
                },
                "configs": [asdict(item) for item in self.configs],
                "deployments": [asdict(item) for item in self.deployments],
                "alerts": {
                    key: asdict(alert) for key, alert in sorted(self.alerts.items())
                },
                "incidents": {
                    key: asdict(incident)
                    for key, incident in sorted(self.incidents.items())
                },
                "orders": [asdict(item) for item in self.orders],
                "actions": [asdict(item) for item in self.actions],
            }
        )
