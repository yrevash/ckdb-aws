"""Shared helper for the live-cluster test files in this directory. Not a
test module itself (no `test_` prefix) -- pytest will not collect it.
"""

from __future__ import annotations

from postmortem_resilience.db import can_connect
from postmortem_resilience.topology import CONTROL_NODE

# True if CONTROL_NODE (crdb-use1-a, us-east-1) accepts a SQL connection.
# Used to skip the live-cluster tests in this package when docker-
# compose.multiregion.yml hasn't been brought up -- mirrors the
# `POSTMORTEM_TEST_DATABASE_URL` skipif convention used elsewhere in the repo
# (backend/tests/test_cockroach_live.py, consolidation/tests/
# test_repository_live.py), just auto-detected instead of env-var-gated,
# since this package's topology is fixed rather than passed in via a DSN.
CLUSTER_REACHABLE = can_connect(CONTROL_NODE)
SKIP_REASON = (
    "docker-compose.multiregion.yml cluster is not reachable at "
    f"{CONTROL_NODE.host}:{CONTROL_NODE.sql_port} -- bring it up first "
    "(scripts/failover_demo.sh or scripts/measure_resilience.sh), or run "
    "`docker compose -f docker-compose.multiregion.yml up -d` manually."
)
