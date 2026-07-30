#!/usr/bin/env sh
# Phase 3, Track A -- run the resilience/failover measurement harness against
# the already-running local simulated multi-region cluster
# (docker-compose.multiregion.yml) and write the JSON telemetry report.
#
# This script assumes the cluster is already up, migrated, and multi-region-
# bootstrapped (SET PRIMARY REGION / ADD REGION / SURVIVE REGION FAILURE) --
# see scripts/failover_demo.sh, which does all of that and then calls this
# script, or bring the cluster up manually per docker-compose.multiregion.yml's
# header comment. Running this against a cluster that isn't ready yet fails
# fast with a clear message rather than hanging.
#
# WARNING: this performs a real `docker compose kill` (SIGKILL) of an entire
# region's 3 nodes and restarts them -- exactly what it's here to measure.
# It only ever touches docker-compose.multiregion.yml's `crdb-*` services,
# never the Phase 1/2 single node (docker-compose.yml's `cockroach` service).
#
# Usage:
#   scripts/measure_resilience.sh [output-json-path]
#
# Default output: evaluation/reports/phase3-resilience.json

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT="${1:-$ROOT_DIR/evaluation/reports/phase3-resilience.json}"
PYTHON="$ROOT_DIR/backend/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "error: $PYTHON not found -- set up backend/.venv first (see backend/README.md / verify_phase1.sh)." >&2
  exit 1
fi

echo "Preflight: checking the multi-region cluster is reachable and bootstrapped..."
if ! PYTHONPATH="$ROOT_DIR/resilience" "$PYTHON" -c "
from postmortem_resilience.db import can_connect
from postmortem_resilience.topology import CONTROL_NODE
import sys
sys.exit(0 if can_connect(CONTROL_NODE, timeout=3.0) else 1)
"; then
  echo "error: the multi-region cluster is not reachable at localhost:26400 (crdb-use1-a)." >&2
  echo "Bring it up first: scripts/failover_demo.sh, or manually:" >&2
  echo "  docker compose -f docker-compose.multiregion.yml up -d" >&2
  echo "  docker compose -f docker-compose.multiregion.yml run --rm mr-init" >&2
  echo "  docker compose -f docker-compose.multiregion.yml run --rm mr-migrate" >&2
  echo "  docker compose -f docker-compose.multiregion.yml run --rm mr-bootstrap-multiregion" >&2
  exit 1
fi

echo "Running the resilience harness (seed -> baseline probes -> kill us-east-2 ->"
echo "measure RTO -> restore -> verify RPO=0)..."
PYTHONPATH="$ROOT_DIR/resilience" "$PYTHON" -m postmortem_resilience --output "$OUTPUT"

echo
echo "Report written to $OUTPUT"
