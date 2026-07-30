"""Pure unit tests for the report shape -- no live cluster required."""

from __future__ import annotations

import json

from postmortem_resilience.report import (
    ProbeResult,
    ResilienceReport,
    overall_from_probes,
)


def _passing_probes() -> dict[str, ProbeResult]:
    return {
        "freshness": ProbeResult("read_your_write", "pass", 1.2, "ms", {}),
        "cross_agent_visibility": ProbeResult("cross_agent_visibility", "pass", 0.9, "ms", {}),
        "atomicity": ProbeResult("atomicity", "pass", 1.0, "bool", {}),
        "rto": ProbeResult("rto", "pass", 3.4, "seconds", {}),
        "rpo": ProbeResult("rpo", "pass", 0.0, "rows_lost", {}),
    }


def test_overall_from_probes_all_pass() -> None:
    overall = overall_from_probes(_passing_probes())
    assert overall["pass"] is True
    assert overall["failed_probes"] == []


def test_overall_from_probes_reports_failures() -> None:
    probes = _passing_probes()
    probes["rto"] = ProbeResult("rto", "fail", 14.0, "seconds", {"reason": "too slow"})
    overall = overall_from_probes(probes)
    assert overall["pass"] is False
    assert overall["failed_probes"] == ["rto"]


def test_probe_result_to_dict_is_json_serializable() -> None:
    probe = ProbeResult("rpo", "pass", 0.0, "rows_lost", {"rows_expected": 5, "rows_found": 5})
    encoded = json.dumps(probe.to_dict())
    decoded = json.loads(encoded)
    assert decoded["probe_type"] == "rpo"
    assert decoded["status"] == "pass"
    assert decoded["details"]["rows_expected"] == 5


def test_resilience_report_write_json_round_trips(tmp_path) -> None:
    probes = _passing_probes()
    report = ResilienceReport(
        generated_at="2026-07-31T00:00:00+00:00",
        topology={"regions": ["us-east-1", "us-east-2", "us-west-2"],
                  "primary_region": "us-east-1", "killed_region": "us-east-2",
                  "nodes_total": 9, "replication_factor": 5},
        run={"org_id": "00000000-0000-0000-0000-000000000001"},
        probes={name: p.to_dict() for name, p in probes.items()},
        node_liveness={"before_kill": 9, "during_outage": 6, "after_recovery": 9},
        range_snapshot={"before_kill": {}, "during_outage": {}, "after_recovery": {}},
        overall=overall_from_probes(probes),
    )

    out_path = tmp_path / "phase3-resilience.json"
    written = report.write_json(out_path)

    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert on_disk == written
    assert on_disk["schema_version"] == "postmortem-resilience-v1"
    assert on_disk["overall"]["pass"] is True
    assert on_disk["node_liveness"]["during_outage"] == 6
    assert set(on_disk["probes"]) == {
        "freshness", "cross_agent_visibility", "atomicity", "rto", "rpo",
    }
