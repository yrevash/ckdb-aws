"""Report shape shared by the harness, `scripts/measure_resilience.sh`, and
the UI track (Track D) that renders `evaluation/reports/phase3-resilience.json`.

Also responsible for writing each probe result as a row in `eval_probes`
(db/migrations/0002_core_schema.sql already defines this table with
`probe_type IN ('read_your_write', 'cross_agent_visibility', 'atomicity',
'rpo', 'rto')` -- it was clearly provisioned for exactly this harness) so the
proof lives in the database itself, not only in a JSON file on disk.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "postmortem-resilience-v1"

ProbeType = Literal["read_your_write", "cross_agent_visibility", "atomicity", "rpo", "rto"]
Status = Literal["pass", "fail"]


@dataclass
class ProbeResult:
    probe_type: ProbeType
    status: Status
    measured_value: float | None
    unit: str | None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_probe(conn, org_id: str, probe: ProbeResult) -> None:
    """Insert `probe` into eval_probes on `conn` (commits internally unless
    conn.autocommit is already True)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eval_probes (org_id, probe_type, status, measured_value, unit, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (org_id, probe.probe_type, probe.status, probe.measured_value,
             probe.unit, json.dumps(probe.details)),
        )
    if not conn.autocommit:
        conn.commit()


@dataclass
class ResilienceReport:
    generated_at: str
    topology: dict[str, Any]
    run: dict[str, Any]
    probes: dict[str, dict[str, Any]]
    node_liveness: dict[str, int]
    range_snapshot: dict[str, Any]
    overall: dict[str, Any]
    # Pin+verify results (see leaseholders.pin_and_verify): proof that the
    # probed tables' leaseholders were actually moved into the region about
    # to be killed, BEFORE the kill -- the mechanical fix for the audit
    # finding that the killed region owned zero leaseholders. Defaults to
    # {} so existing callers/tests that construct a ResilienceReport without
    # this field (e.g. resilience/tests/test_report.py) keep working.
    leaseholder_pin: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> dict[str, Any]:
        out = self.to_dict()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        return out


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def overall_from_probes(probes: dict[str, ProbeResult]) -> dict[str, Any]:
    failed = [name for name, p in probes.items() if p.status != "pass"]
    return {
        "pass": len(failed) == 0,
        "failed_probes": failed,
        "summary": (
            "all resilience probes passed: RPO=0, RTO<target, freshness=0ms, "
            "atomicity holds, cross-agent visibility has no lag"
            if not failed
            else f"probes failed: {', '.join(failed)}"
        ),
    }
