"""Pure unit tests -- no live cluster required. Cross-checks
postmortem_resilience.topology against docker-compose.multiregion.yml so the
two definitions of "what nodes exist" can't silently drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from postmortem_resilience.topology import (
    ALL_REGIONS,
    CONTROL_NODE,
    DEFAULT_KILL_REGION,
    PRIMARY_REGION,
    TOPOLOGY,
    nodes_in_region,
    services_in_region,
    surviving_nodes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.multiregion.yml"


def test_topology_has_nine_nodes_three_regions_three_zones_each() -> None:
    assert len(TOPOLOGY) == 9
    for region in ALL_REGIONS:
        assert len(nodes_in_region(region)) == 3


def test_all_node_services_and_ports_are_unique() -> None:
    services = [n.service for n in TOPOLOGY]
    sql_ports = [n.sql_port for n in TOPOLOGY]
    http_ports = [n.http_port for n in TOPOLOGY]
    assert len(services) == len(set(services))
    assert len(sql_ports) == len(set(sql_ports))
    assert len(http_ports) == len(set(http_ports))


def test_ports_do_not_clash_with_phase1_phase2_single_node() -> None:
    for node in TOPOLOGY:
        assert node.sql_port != 26257
        assert node.http_port != 8080


def test_control_node_is_in_primary_region_and_never_the_kill_target() -> None:
    assert CONTROL_NODE.region == PRIMARY_REGION
    assert CONTROL_NODE.region != DEFAULT_KILL_REGION


def test_surviving_nodes_excludes_killed_region() -> None:
    survivors = surviving_nodes(DEFAULT_KILL_REGION)
    assert len(survivors) == 6
    assert all(n.region != DEFAULT_KILL_REGION for n in survivors)


def test_dsn_shape() -> None:
    dsn = CONTROL_NODE.dsn()
    assert dsn.startswith("postgresql://root@")
    assert f":{CONTROL_NODE.sql_port}/postmortem" in dsn
    assert dsn.endswith("sslmode=disable")


def test_topology_matches_compose_file_service_names_and_ports() -> None:
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load(COMPOSE_PATH.read_text())
    services = compose["services"]

    for node in TOPOLOGY:
        assert node.service in services, f"{node.service} missing from {COMPOSE_PATH.name}"
        ports = services[node.service]["ports"]
        port_pairs = [p.split(":") for p in ports]
        host_ports = {int(pair[0]) for pair in port_pairs}
        assert node.sql_port in host_ports, f"{node.service}: expected SQL port {node.sql_port} in {ports}"
        assert node.http_port in host_ports, f"{node.service}: expected HTTP port {node.http_port} in {ports}"

        command = services[node.service]["command"]
        locality_flag = f"--locality=region={node.region},zone={node.zone}"
        assert locality_flag in command, (
            f"{node.service}: expected {locality_flag!r} in command {command!r}"
        )


def test_services_in_region_matches_compose_naming_convention() -> None:
    for region in ALL_REGIONS:
        services = services_in_region(region)
        assert len(services) == 3
        assert all(s.startswith("crdb-") for s in services)
