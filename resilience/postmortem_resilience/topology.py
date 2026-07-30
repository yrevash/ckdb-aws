"""Static description of the local simulated multi-region cluster.

This mirrors docker-compose.multiregion.yml exactly (service names, host
ports, `--locality=region=...,zone=...` flags). Keeping it as data here
(rather than parsing the compose file at runtime) means the harness has no
YAML dependency in its live/runtime path; resilience/tests/test_topology.py
cross-checks this module against the compose file so the two can't silently
drift apart.

Connections are made from the host machine, so DSNs use `localhost` and the
host-side port each node's `ports:` mapping in the compose file exposes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

COMPOSE_FILE = "docker-compose.multiregion.yml"
COMPOSE_PROJECT_NAME = "postmortem-multiregion"  # matches the `name:` field
DATABASE_NAME = "postmortem"

PRIMARY_REGION = "us-east-1"
DEFAULT_KILL_REGION = "us-east-2"
THIRD_REGION = "us-west-2"
ALL_REGIONS = (PRIMARY_REGION, DEFAULT_KILL_REGION, THIRD_REGION)


@dataclass(frozen=True)
class Node:
    """One CockroachDB node in the simulated cluster."""

    service: str  # docker compose service name, e.g. "crdb-use1-a"
    region: str
    zone: str
    host: str
    sql_port: int
    http_port: int

    @property
    def name(self) -> str:
        return self.service

    def dsn(self, *, database: str = DATABASE_NAME, user: str = "root") -> str:
        return (
            f"postgresql://{user}@{self.host}:{self.sql_port}/{database}"
            "?sslmode=disable"
        )


def _default_host() -> str:
    return os.environ.get("RESILIENCE_DB_HOST", "localhost")


def _build_default_topology() -> tuple[Node, ...]:
    host = _default_host()
    # (service, region, zone, sql_port, http_port) -- must match
    # docker-compose.multiregion.yml's `ports:` mappings exactly.
    layout = [
        ("crdb-use1-a", "us-east-1", "a", 26400, 8090),
        ("crdb-use1-b", "us-east-1", "b", 26401, 8091),
        ("crdb-use1-c", "us-east-1", "c", 26402, 8092),
        ("crdb-use2-a", "us-east-2", "a", 26403, 8093),
        ("crdb-use2-b", "us-east-2", "b", 26404, 8094),
        ("crdb-use2-c", "us-east-2", "c", 26405, 8095),
        ("crdb-usw2-a", "us-west-2", "a", 26406, 8096),
        ("crdb-usw2-b", "us-west-2", "b", 26407, 8097),
        ("crdb-usw2-c", "us-west-2", "c", 26408, 8098),
    ]
    return tuple(
        Node(service=service, region=region, zone=zone, host=host,
             sql_port=sql_port, http_port=http_port)
        for service, region, zone, sql_port, http_port in layout
    )


TOPOLOGY: tuple[Node, ...] = _build_default_topology()

# The node the harness/CLI queries for cluster-wide facts (`cockroach node
# status`, `SHOW RANGES ...`). Must be in a region that scripts/failover_demo.sh
# never kills, so it stays reachable throughout the outage window.
CONTROL_NODE = TOPOLOGY[0]  # crdb-use1-a, us-east-1


def nodes_in_region(region: str, topology: tuple[Node, ...] = TOPOLOGY) -> tuple[Node, ...]:
    return tuple(n for n in topology if n.region == region)


def services_in_region(region: str, topology: tuple[Node, ...] = TOPOLOGY) -> tuple[str, ...]:
    return tuple(n.service for n in nodes_in_region(region, topology))


def surviving_nodes(killed_region: str, topology: tuple[Node, ...] = TOPOLOGY) -> tuple[Node, ...]:
    return tuple(n for n in topology if n.region != killed_region)
