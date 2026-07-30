#!/usr/bin/env sh
# Phase 3 verification -- orchestrates the Track A deliverables end to end:
# brings up the local simulated multi-region CockroachDB cluster, applies
# migrations + the SURVIVE REGION FAILURE bootstrap, runs the resilience
# harness's pytest suite (including a real region-kill-and-recover cycle;
# see resilience/tests/test_live_resilience.py), and then runs the harness
# once more standalone to (re)produce evaluation/reports/phase3-resilience.json.
#
# Mirrors the shape of scripts/verify_phase1.sh / scripts/verify_phase2.sh:
# each phase's verifier brings up whatever infrastructure that phase owns and
# runs that phase's tests. This does not touch or start docker-compose.yml's
# Phase 1/2 single node.
#
# Track B (bitemporal facts) and Track C (production hardening) suites are
# appended below by whoever owns those tracks -- see the TODO markers.

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE_FILE="$ROOT_DIR/docker-compose.multiregion.yml"
COMPOSE="docker compose --project-directory $ROOT_DIR -f $COMPOSE_FILE"

echo "== Phase 3 verification: Track A (resilience & failover) =="

echo "-- Bringing up the multi-region cluster..."
$COMPOSE up -d

echo "-- Waiting for all 9 nodes to report healthy..."
i=0
until [ "$($COMPOSE ps --format '{{.Name}} {{.Health}}' | grep -vc healthy)" -eq 0 ]; do
  i=$((i + 1))
  if [ "$i" -gt 90 ]; then
    echo "error: nodes did not become healthy within ~90s" >&2
    $COMPOSE ps
    exit 1
  fi
  sleep 1
done

echo "-- Initializing the cluster (idempotent)..."
$COMPOSE run --rm mr-init

echo "-- Applying schema migrations (idempotent, ledger-tracked)..."
$COMPOSE run --rm mr-migrate >/dev/null

echo "-- Converting the postmortem database to 3-region SURVIVE REGION FAILURE (idempotent)..."
$COMPOSE run --rm mr-bootstrap-multiregion >/dev/null

echo "-- Running the resilience/ pytest suite against the live cluster"
echo "   (includes a real region-kill-and-recover cycle: resilience/tests/test_live_resilience.py)..."
(
  cd "$ROOT_DIR/resilience"
  PYTHONPATH="$ROOT_DIR/resilience" "$ROOT_DIR/backend/.venv/bin/pytest" -q tests
)

echo "-- Producing evaluation/reports/phase3-resilience.json (a second, independent"
echo "   region-kill-and-recover cycle -- the exit gate calls for reproducibility,"
echo "   not a single lucky run)..."
"$ROOT_DIR/scripts/measure_resilience.sh"

# TODO(integration): Track B temporal suite
# Bitemporal fact transitions + temporal-drift evaluation
# (research/postmortem/07-... / docs/PHASE3_PLAN.md Track B). Append here:
#   (cd "$ROOT_DIR/..." && ... run Track B's tests/evaluation ...)

# TODO(integration): Track C hardening suite
# Audit logging, backup/PITR smoke test, Agent-Skills findings, least-
# privilege grant checks (docs/PHASE3_PLAN.md Track C). Append here:
#   (cd "$ROOT_DIR/..." && ... run Track C's tests/checks ...)

echo "Phase 3 (resilience) verification passed."
