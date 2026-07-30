#!/usr/bin/env sh
# Track C production hardening: verifies that
#   1. db/migrations/0007_audit_logging.sql's reader/writer roles enforce the
#      recall-vs-act least-privilege split at the SQL grant level, and
#   2. writes made through the writer role actually produce SQL audit-log
#      entries (both the table-level EXPERIMENTAL_AUDIT trail and the
#      role-based sql.log.user_audit trail).
#
# Fully self-contained and reproducible from a clean node: spins up its own
# throwaway CockroachDB container (distinct port from the shared dev cluster,
# any other track's cluster, and the multi-region demo ports), applies every
# migration through the same db/apply.sh the rest of the project uses, runs a
# scripted sequence of allowed/denied reads and writes, greps the container's
# own log files for the resulting audit events, and tears itself down.
#
# Usage: scripts/audit_check.sh
# Env:
#   AUDIT_CHECK_PORT      host port for the throwaway node (default 26268)
#   AUDIT_CHECK_CONTAINER container name (default pm_hardening_audit)
#   AUDIT_CHECK_IMAGE     cockroach image (default cockroachdb/cockroach:v26.2.0)
#   KEEP_CONTAINER=1      skip teardown on exit, for debugging

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PORT="${AUDIT_CHECK_PORT:-26268}"
CONTAINER="${AUDIT_CHECK_CONTAINER:-pm_hardening_audit}"
IMAGE="${AUDIT_CHECK_IMAGE:-cockroachdb/cockroach:v26.2.0}"
WORKDIR=$(mktemp -d)

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "PASS: $1"
}

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

crexec() { docker exec -i "$CONTAINER" /cockroach/cockroach "$@"; }
cr()     { crexec sql --insecure --database=postmortem -e "$1"; }
cr_as()  { user=$1; shift; crexec sql --insecure --user="$user" --database=postmortem -e "$1"; }

echo "Waiting for node readiness ..."
i=0
until crexec sql --insecure -e "SELECT 1;" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then fail "node did not become ready within 30s"; fi
  sleep 1
done

# db/apply.sh drives migrations through a `cockroach` binary on COCKROACH_BIN.
# Wrap `docker exec` as that binary so the exact same apply script the rest of
# the project uses (and the exact same migration files, including 0007) runs
# unmodified against this throwaway node.
CR_WRAPPER="$WORKDIR/cockroach"
cat >"$CR_WRAPPER" <<EOF
#!/bin/sh
exec docker exec -i "$CONTAINER" /cockroach/cockroach "\$@"
EOF
chmod +x "$CR_WRAPPER"

echo "Applying migrations (0001-0007) ..."
(
  cd "$ROOT_DIR/db"
  DATABASE_URL="postgresql://root@localhost:26257?sslmode=disable" \
    COCKROACH_BIN="$CR_WRAPPER" \
    ./apply.sh
) >"$WORKDIR/apply.log" 2>&1 || {
  cat "$WORKDIR/apply.log" >&2
  fail "migration apply failed -- see log above"
}
grep -q "apply 0007_audit_logging" "$WORKDIR/apply.log" \
  || grep -q "skip 0007_audit_logging" "$WORKDIR/apply.log" \
  || fail "0007_audit_logging never ran"
pass "migrations 0001-0007 applied cleanly"

# --- 1. Grant-shape assertions -------------------------------------------
WRITER_INSERT_TABLES=$(crexec sql --insecure --database=postmortem --format=csv \
  -e "SELECT object_name FROM [SHOW GRANTS FOR postmortem_writer] WHERE privilege_type = 'INSERT' ORDER BY object_name;" \
  | tail -n +2 | tr '\n' ',')
[ "$WRITER_INSERT_TABLES" = "deploys,episodic_events,remediation_actions," ] \
  || fail "postmortem_writer INSERT grants drifted from expected atomic-write-path tables: got [$WRITER_INSERT_TABLES]"
pass "postmortem_writer INSERT is scoped to exactly {deploys, episodic_events, remediation_actions}"

WRITER_HAS_SEMANTIC=$(crexec sql --insecure --database=postmortem --format=csv \
  -e "SELECT count(*) FROM [SHOW GRANTS FOR postmortem_writer] WHERE object_name = 'semantic_facts';" | tail -n 1)
[ "$WRITER_HAS_SEMANTIC" = "0" ] || fail "postmortem_writer unexpectedly has a grant on semantic_facts (consolidation-owned table)"
pass "postmortem_writer has zero privileges on semantic_facts (consolidation job's table, not the agent's)"

READER_WRITE_GRANTS=$(crexec sql --insecure --database=postmortem --format=csv \
  -e "SELECT count(*) FROM [SHOW GRANTS FOR postmortem_reader] WHERE privilege_type IN ('INSERT','UPDATE','DELETE');" | tail -n 1)
[ "$READER_WRITE_GRANTS" = "0" ] || fail "postmortem_reader has $READER_WRITE_GRANTS write grant(s); reader must be SELECT-only"
pass "postmortem_reader is SELECT-only (0 INSERT/UPDATE/DELETE grants)"

AUDIT_SETTING=$(crexec sql --insecure --database=postmortem --format=csv \
  -e "SHOW CLUSTER SETTING sql.log.user_audit;" | tail -n 1)
[ "$AUDIT_SETTING" = "postmortem_writer ALL" ] || fail "sql.log.user_audit = '$AUDIT_SETTING', expected 'postmortem_writer ALL'"
pass "sql.log.user_audit = 'postmortem_writer ALL'"

# --- 2. Seed minimal FK targets as root (writer has no INSERT on these) --
MARKER="audit-check-$(date +%s)"
cr "
INSERT INTO organizations (org_id, slug, display_name)
  VALUES ('00000000-0000-0000-0000-0000000000a1','audit-check-org','Audit Check Org')
  ON CONFLICT DO NOTHING;
INSERT INTO services (service_id, org_id, name, current_version)
  VALUES ('00000000-0000-0000-0000-0000000000a2','00000000-0000-0000-0000-0000000000a1','audit-check-svc','v1')
  ON CONFLICT DO NOTHING;
INSERT INTO incidents (incident_id, org_id, service_id, title, severity)
  VALUES ('00000000-0000-0000-0000-0000000000a3','00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000a2','audit check incident','SEV3')
  ON CONFLICT DO NOTHING;
" >/dev/null

# --- 3. Enforcement: denied writes/reads must actually fail --------------
if cr_as postmortem_agent_writer \
  "INSERT INTO semantic_facts (org_id, agent_id, subject, predicate, object) VALUES ('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000a2','x','y','{}');" \
  >/dev/null 2>&1
then
  fail "postmortem_agent_writer was able to write semantic_facts -- least-privilege grant is broken"
fi
pass "postmortem_agent_writer is denied INSERT on semantic_facts (out of scope for the Act path)"

if cr_as postmortem_agent_reader \
  "INSERT INTO episodic_events (org_id, agent_id, event_type) VALUES ('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000a2','observation');" \
  >/dev/null 2>&1
then
  fail "postmortem_agent_reader was able to write episodic_events -- reader is not read-only"
fi
pass "postmortem_agent_reader is denied INSERT on episodic_events"

# --- 4. The audited action: a real write on the writer's own path --------
cr_as postmortem_agent_writer \
  "INSERT INTO episodic_events (org_id, agent_id, incident_id, event_type, content) VALUES ('00000000-0000-0000-0000-0000000000a1','00000000-0000-0000-0000-0000000000a2','00000000-0000-0000-0000-0000000000a3','action','$MARKER');" \
  >/dev/null

ROWCOUNT=$(crexec sql --insecure --user=postmortem_agent_reader --database=postmortem --format=csv \
  -e "SELECT count(*) FROM episodic_events WHERE content = '$MARKER';" | tail -n 1)
[ "$ROWCOUNT" = "1" ] || fail "expected 1 row for marker $MARKER via reader SELECT, got $ROWCOUNT"
pass "postmortem_agent_writer's write is immediately visible to postmortem_agent_reader (same marker: $MARKER)"

# --- 5. Prove the write landed in BOTH audit trails -----------------------
if ! docker exec "$CONTAINER" sh -c "grep -l '$MARKER' /cockroach/cockroach-data/logs/cockroach-sql-audit.*.log" >/dev/null 2>&1; then
  fail "no cockroach-sql-audit log file contains marker $MARKER"
fi
AUDIT_LOG_LINE=$(docker exec "$CONTAINER" sh -c "grep '$MARKER' /cockroach/cockroach-data/logs/cockroach-sql-audit.*.log")

echo "$AUDIT_LOG_LINE" | grep -q '"EventType":"sensitive_table_access"' \
  || fail "table-level EXPERIMENTAL_AUDIT (sensitive_table_access) entry not found for marker write"
pass "table-level audit (EXPERIMENTAL_AUDIT) captured the write"

echo "$AUDIT_LOG_LINE" | grep -q '"EventType":"role_based_audit_event"' \
  || fail "role-based audit (sql.log.user_audit) entry not found for marker write"
# Note: CockroachDB redacts identifiers between Unicode guillemets (<U+2039>...
# <U+203A>), e.g. "Role":"<guillemet>postmortem_writer<guillemet>" -- match
# loosely rather than assuming a fixed byte width around the value.
echo "$AUDIT_LOG_LINE" | grep -q 'Role.*postmortem_writer' \
  || fail "role-based audit entry did not tag Role=postmortem_writer"
pass "role-based audit (sql.log.user_audit) captured the write, tagged Role=postmortem_writer"

echo "$AUDIT_LOG_LINE" | grep -q 'User.*postmortem_agent_writer' \
  || fail "audit entry did not tag the executing principal postmortem_agent_writer"
pass "audit entry attributes the write to principal postmortem_agent_writer"

# --- 6. Denied attempts are ALSO audited (a security-relevant property) --
DENIED_LOG_COUNT=$(docker exec "$CONTAINER" sh -c \
  "grep -c 'postmortem_agent_writer.*semantic_facts.*42501' /cockroach/cockroach-data/logs/cockroach-sql-audit.*.log 2>/dev/null || true" | head -n1)
[ "${DENIED_LOG_COUNT:-0}" -ge 1 ] 2>/dev/null || fail "the denied semantic_facts write attempt was not itself audited"
pass "the denied privilege-violation attempt is itself present in the audit log (SQLSTATE 42501)"

echo ""
echo "All audit/RBAC checks passed."
