#!/usr/bin/env sh
# Phase 3, Track A -- the failover "money shot" rehearsal.
#
# Brings up the local simulated 9-node / 3-region CockroachDB cluster
# (docker-compose.multiregion.yml), applies the schema migrations and the
# multi-region bootstrap (SURVIVE REGION FAILURE), then kills an entire
# region (us-east-2, 3 nodes) in a controlled, reproducible way, drives a
# scripted write/read through the surviving quorum while the region is down,
# restores it, and prints the resulting RPO/RTO/atomicity/freshness/
# cross-agent-visibility telemetry.
#
# Idempotent and re-runnable: every step it performs (`docker compose up`,
# `mr-init`, `mr-migrate`, `mr-bootstrap-multiregion`) is safe to run again
# against a cluster that's already in the target state -- see each of those
# steps' own idempotency notes in docker-compose.multiregion.yml and
# db/bootstrap/010_multiregion.sql. Re-running this script performs a fresh,
# independent region-kill-and-recover cycle every time, which is the point:
# research/postmortem/04-cockroachdb-deployment-resilience.md's exit gate
# calls for "three successful dress rehearsals, reproducibly."
#
# This script NEVER touches docker-compose.yml's single-node Phase 1/2
# cluster (distinct compose project, distinct ports, distinct network).
#
# Usage:
#   scripts/failover_demo.sh
#
# Environment:
#   RESILIENCE_TEARDOWN=1   tear the multi-region cluster down (docker
#                           compose down -v) after the rehearsal completes.
#                           Default: leave it running, healthy, so an
#                           operator can keep inspecting it or re-run the
#                           harness again without paying the ~15s cluster
#                           bring-up cost a second time.

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE_FILE="$ROOT_DIR/docker-compose.multiregion.yml"
COMPOSE="docker compose --project-directory $ROOT_DIR -f $COMPOSE_FILE"
REPORT_PATH="$ROOT_DIR/evaluation/reports/phase3-resilience.json"

echo "== Phase 3, Track A -- failover demo (9 nodes, 3 regions x 3 zones) =="
echo

echo "-- Bringing up the multi-region cluster (docker-compose.multiregion.yml)..."
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
echo "   all 9 nodes healthy."

echo "-- Initializing the cluster (idempotent no-op if already initialized)..."
$COMPOSE run --rm mr-init

echo "-- Applying schema migrations (idempotent, ledger-tracked via schema_migrations)..."
$COMPOSE run --rm mr-migrate >/dev/null

echo "-- Converting the postmortem database to 3-region SURVIVE REGION FAILURE"
echo "   (PRIMARY REGION us-east-1, ADD REGION us-east-2/us-west-2, RF=5 2+2+1;"
echo "   idempotent -- see db/bootstrap/010_multiregion.sql)..."
$COMPOSE run --rm mr-bootstrap-multiregion >/dev/null

echo
echo "-- Running the region-kill rehearsal: kills us-east-2's 3 nodes live,"
echo "   drives a scripted write/read through the surviving 6-node quorum,"
echo "   restores the region, and measures RPO/RTO/freshness/atomicity/"
echo "   cross-agent visibility (scripts/measure_resilience.sh)."
echo "   [narration] us-east-2 (crdb-use2-a/b/c) goes down live now, on this"
echo "   run's clock -- see node_liveness/rto in the telemetry below for the"
echo "   exact wall-clock timestamps of the kill and recovery."
echo
"$ROOT_DIR/scripts/measure_resilience.sh" "$REPORT_PATH"

echo
echo "== Telemetry ($REPORT_PATH) =="
"$ROOT_DIR/backend/.venv/bin/python" - "$REPORT_PATH" <<'PYEOF'
import json
import sys

report = json.load(open(sys.argv[1]))
t = report["topology"]
n = report["node_liveness"]
p = report["probes"]

print(f"Regions:            {', '.join(t['regions'])} (primary: {t['primary_region']})")
print(f"Region killed:      {t['killed_region']}  (RF={t['replication_factor']}, {t['nodes_total']} nodes total)")
print(f"Node liveness:      {n['before_kill']} -> {n['during_outage']} -> {n['after_recovery']}"
      f"  (region-down detected in {n['region_down_detection_seconds']}s;"
      f" full recovery in {n['recovery_elapsed_seconds']}s)")
print()
print(f"RPO (rows lost):    {p['rpo']['measured_value']:.0f}  [target: 0]           -> {p['rpo']['status'].upper()}")
rto_val = p["rto"]["measured_value"]
rto_str = f"{rto_val:.3f}s" if rto_val is not None else "n/a"
print(f"RTO:                {rto_str}  [target: < {p['rto']['details']['target_seconds']}s]     -> {p['rto']['status'].upper()}")
print(f"Freshness:          {p['freshness']['measured_value']:.1f}ms round trip, found immediately -> {p['freshness']['status'].upper()}")
print(f"Cross-agent lag:    {p['cross_agent_visibility']['measured_value']:.1f}ms round trip, cross-region"
      f" -> {p['cross_agent_visibility']['status'].upper()}")
print(f"Atomicity:          commit+abort both hold together                -> {p['atomicity']['status'].upper()}")
print()
print(f"Overall: {'PASS' if report['overall']['pass'] else 'FAIL'} -- {report['overall']['summary']}")
PYEOF

echo
if [ "${RESILIENCE_TEARDOWN:-0}" = "1" ]; then
  echo "-- RESILIENCE_TEARDOWN=1: tearing down the multi-region cluster..."
  $COMPOSE down -v
else
  echo "-- Cluster left running (crdb-use1-a on localhost:26400, console on :8090)."
  echo "   Re-run this script any time for another independent rehearsal, or tear down with:"
  echo "     docker compose -f docker-compose.multiregion.yml down -v"
fi
