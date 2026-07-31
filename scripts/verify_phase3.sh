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

echo "== Phase 3 verification: Track B (bitemporal facts & temporal drift) =="
# Run the temporal suites against the live multi-region primary (us-east-1 node
# on :26400) -- proves bitemporal transitions + currently-valid recall + the
# temporal-validity metric hold under the SURVIVE REGION FAILURE topology, not
# just on a single node. The live bitemporal test is skip-guarded on
# POSTMORTEM_TEST_DATABASE_URL.
MR_PRIMARY_URL="postgresql://root@localhost:26400/postmortem?sslmode=disable"
(
  cd "$ROOT_DIR"
  POSTMORTEM_TEST_DATABASE_URL="$MR_PRIMARY_URL" \
    PYTHONPATH="$ROOT_DIR/backend/src:$ROOT_DIR/simulator:$ROOT_DIR/evaluation" \
    "$ROOT_DIR/backend/.venv/bin/pytest" -q \
      backend/tests/test_bitemporal_recall.py \
      backend/tests/test_bitemporal_live.py \
      simulator/tests/test_temporal_drift.py \
      evaluation/tests/test_temporal_validity.py
)

echo "== Phase 3 verification: Track C (production hardening) =="
# Both scripts are fully self-contained: each spins its own throwaway node
# (:26268 / :26269), applies every migration via db/apply.sh (which now also
# applies bootstrap/090's cluster settings), proves its property, and tears
# down. They do not touch the multi-region cluster or the Phase 1/2 node.
"$ROOT_DIR/scripts/audit_check.sh"
"$ROOT_DIR/scripts/backup_pitr_smoke.sh"

echo "Phase 3 verification passed (Track A resilience + Track B temporal + Track C hardening)."
