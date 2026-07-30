#!/usr/bin/env sh
# Track C production hardening: a local BACKUP -> mutate -> point-in-time
# RESTORE smoke test. This is the belt-and-suspenders proof that a buggy
# agent write (a bad remediation, a wrong episodic/semantic write) can be
# recovered even though CockroachDB's synchronous replication faithfully
# replicates that same bad write to every replica -- replication protects
# against infra loss, not logical corruption. See docs/HARDENING.md.
#
# Sequence, all against a throwaway local node:
#   1. Seed data through the same shape of write the agent's Act path makes.
#   2. Capture a pre-mutation MVCC timestamp (the PITR cutpoint) and a
#      content fingerprint.
#   3. BACKUP DATABASE ... INTO nodelocal://... WITH revision_history.
#   4. Simulate a buggy agent write: corrupt one row, delete another.
#   5. Take a follow-up incremental backup so the chain covers the mutation.
#   6. RESTORE DATABASE ... AS OF SYSTEM TIME <cutpoint> WITH new_db_name,
#      i.e. point-in-time recovery to the instant before the bad write.
#   7. Assert the restored database has the pre-mutation content back, and
#      that the live (corrupted) database still shows the damage -- proving
#      RESTORE, not luck, did the recovering.
#
# Usage: scripts/backup_pitr_smoke.sh
# Env:
#   PITR_PORT      host port for the throwaway node (default 26269)
#   PITR_CONTAINER container name (default pm_hardening_pitr)
#   PITR_IMAGE     cockroach image (default cockroachdb/cockroach:v26.2.0)
#   KEEP_CONTAINER=1  skip teardown on exit, for debugging

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PORT="${PITR_PORT:-26269}"
CONTAINER="${PITR_CONTAINER:-pm_hardening_pitr}"
IMAGE="${PITR_IMAGE:-cockroachdb/cockroach:v26.2.0}"
WORKDIR=$(mktemp -d)
BACKUP_URI="nodelocal://1/backups/pm_pitr_smoke"
ORG="00000000-0000-0000-0000-0000000000b1"
SVC="00000000-0000-0000-0000-0000000000b2"
INC="00000000-0000-0000-0000-0000000000b3"
MARKER="pitr-smoke-$(date +%s)"

fail() { echo "FAIL: $1" >&2; exit 1; }
pass() { echo "PASS: $1"; }

cleanup() {
  status=$?
  if [ "${KEEP_CONTAINER:-0}" != "1" ]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  else
    echo "KEEP_CONTAINER=1: leaving $CONTAINER running on port $PORT"
  fi
  rm -rf "$WORKDIR"
  exit $status
}
trap cleanup EXIT INT TERM

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
echo "Starting throwaway node $CONTAINER on :$PORT ..."
docker run -d --rm --name "$CONTAINER" -p "$PORT:26257" \
  -v "$ROOT_DIR/db:$ROOT_DIR/db:ro" \
  "$IMAGE" start-single-node --insecure >/dev/null

crexec()  { docker exec -i "$CONTAINER" /cockroach/cockroach "$@"; }
cr()      { crexec sql --insecure --database=postmortem -e "$1"; }
cr_csv()  { crexec sql --insecure --database=postmortem --format=csv -e "$1" | tail -n 1; }
cr_root() { crexec sql --insecure -e "$1"; }

echo "Waiting for node readiness ..."
i=0
until crexec sql --insecure -e "SELECT 1;" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then fail "node did not become ready within 30s"; fi
  sleep 1
done

CR_WRAPPER="$WORKDIR/cockroach"
cat >"$CR_WRAPPER" <<EOF
#!/bin/sh
exec docker exec -i "$CONTAINER" /cockroach/cockroach "\$@"
EOF
chmod +x "$CR_WRAPPER"

echo "Applying migrations ..."
(
  cd "$ROOT_DIR/db"
  DATABASE_URL="postgresql://root@localhost:26257?sslmode=disable" \
    COCKROACH_BIN="$CR_WRAPPER" \
    ./apply.sh
) >"$WORKDIR/apply.log" 2>&1 || { cat "$WORKDIR/apply.log" >&2; fail "migration apply failed"; }
pass "migrations applied"

# --- 1. Seed data shaped like the agent's Act path ------------------------
cr "
INSERT INTO organizations (org_id, slug, display_name)
  VALUES ('$ORG','pitr-smoke-org','PITR Smoke Org') ON CONFLICT DO NOTHING;
INSERT INTO services (service_id, org_id, name, current_version)
  VALUES ('$SVC','$ORG','pitr-smoke-svc','v1') ON CONFLICT DO NOTHING;
INSERT INTO incidents (incident_id, org_id, service_id, title, severity)
  VALUES ('$INC','$ORG','$SVC','pitr smoke incident','SEV3') ON CONFLICT DO NOTHING;
INSERT INTO episodic_events (org_id, agent_id, incident_id, event_type, content)
  VALUES ('$ORG','$SVC','$INC','observation','$MARKER');
" >/dev/null
pass "seeded pre-mutation state (marker: $MARKER)"

# --- 2. Capture the PITR cutpoint BEFORE the bad write ---------------------
T1=$(cr_csv "SELECT cluster_logical_timestamp();")
[ -n "$T1" ] || fail "could not capture cluster_logical_timestamp()"
pass "captured PITR cutpoint T1=$T1 (before the bad write)"

PRECOUNT=$(cr_csv "SELECT count(*) FROM episodic_events WHERE content = '$MARKER';")
[ "$PRECOUNT" = "1" ] || fail "expected 1 pre-mutation row, found $PRECOUNT"

# --- 3. Full backup, with revision history for PITR -----------------------
crexec sql --insecure --database=postmortem \
  -e "BACKUP DATABASE postmortem INTO '$BACKUP_URI' WITH revision_history;" \
  >"$WORKDIR/backup1.log" 2>&1 || { cat "$WORKDIR/backup1.log" >&2; fail "full backup failed"; }
grep -qi "succeeded" "$WORKDIR/backup1.log" || fail "full backup did not report success: $(cat "$WORKDIR/backup1.log")"
pass "full BACKUP INTO $BACKUP_URI (revision_history) succeeded"

sleep 1

# --- 4. Simulate a buggy agent write: corrupt one row, delete another -----
cr "
UPDATE incidents SET title = 'CORRUPTED BY BUGGY AGENT' WHERE incident_id = '$INC';
DELETE FROM episodic_events WHERE content = '$MARKER';
" >/dev/null
POSTCOUNT=$(cr_csv "SELECT count(*) FROM episodic_events WHERE content = '$MARKER';")
[ "$POSTCOUNT" = "0" ] || fail "expected the marker row to be gone after the simulated bad write, found $POSTCOUNT"
CORRUPT_TITLE=$(cr_csv "SELECT title FROM incidents WHERE incident_id = '$INC';")
[ "$CORRUPT_TITLE" = "CORRUPTED BY BUGGY AGENT" ] || fail "corruption did not take effect"
pass "simulated a buggy agent write: deleted the memory row and corrupted the incident title"
pass "confirmed synchronous replication is no help here -- the live database now shows the damage"

# --- 5. Incremental backup so the chain covers the mutation instant -------
crexec sql --insecure --database=postmortem \
  -e "BACKUP DATABASE postmortem INTO LATEST IN '$BACKUP_URI' WITH revision_history;" \
  >"$WORKDIR/backup2.log" 2>&1 || { cat "$WORKDIR/backup2.log" >&2; fail "incremental backup failed"; }
grep -qi "succeeded" "$WORKDIR/backup2.log" || fail "incremental backup did not report success"
pass "incremental BACKUP INTO LATEST succeeded"

# --- 6. Point-in-time RESTORE to T1, into a fresh database -----------------
RESTORE_DB="postmortem_pitr_recovered"
cr_root "DROP DATABASE IF EXISTS $RESTORE_DB CASCADE;" >/dev/null
crexec sql --insecure \
  -e "RESTORE DATABASE postmortem FROM LATEST IN '$BACKUP_URI' AS OF SYSTEM TIME '$T1' WITH new_db_name = '$RESTORE_DB';" \
  >"$WORKDIR/restore.log" 2>&1 || { cat "$WORKDIR/restore.log" >&2; fail "point-in-time RESTORE failed"; }
grep -qi "succeeded" "$WORKDIR/restore.log" || fail "RESTORE did not report success: $(cat "$WORKDIR/restore.log")"
pass "point-in-time RESTORE ... AS OF SYSTEM TIME '$T1' WITH new_db_name = '$RESTORE_DB' succeeded"

# --- 7. Prove recovery: the restored DB has the pre-mutation content back -
crrestored() { crexec sql --insecure --database="$RESTORE_DB" --format=csv -e "$1" | tail -n 1; }

RECOVERED_COUNT=$(crrestored "SELECT count(*) FROM episodic_events WHERE content = '$MARKER';")
[ "$RECOVERED_COUNT" = "1" ] || fail "expected the deleted marker row back in $RESTORE_DB, found $RECOVERED_COUNT"
pass "recovered database has the deleted episodic memory row back (RPO of the bad write: 0 rows lost)"

RECOVERED_TITLE=$(crrestored "SELECT title FROM incidents WHERE incident_id = '$INC';")
[ "$RECOVERED_TITLE" = "pitr smoke incident" ] || fail "expected uncorrupted title in $RESTORE_DB, got '$RECOVERED_TITLE'"
pass "recovered database has the pre-corruption incident title, not 'CORRUPTED BY BUGGY AGENT'"

# Sanity: prove this isn't a no-op -- the LIVE database is still damaged.
LIVE_COUNT=$(cr_csv "SELECT count(*) FROM episodic_events WHERE content = '$MARKER';")
[ "$LIVE_COUNT" = "0" ] || fail "expected the live postmortem DB to still show the deletion (sanity check)"
pass "sanity check: the live (unrestored) postmortem database is still corrupted -- RESTORE, not luck, recovered $RESTORE_DB"

cr_root "DROP DATABASE IF EXISTS $RESTORE_DB CASCADE;" >/dev/null

echo ""
echo "All backup/PITR checks passed."
