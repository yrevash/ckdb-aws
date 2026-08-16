#!/bin/sh

set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"

DB_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COCKROACH_BIN=${COCKROACH_BIN:-cockroach}

# --- Cluster-setting bootstrap policy (audit B3) ---------------------------
# Managed CockroachDB Cloud tiers refuse `SET CLUSTER SETTING` for non-admin /
# serverless identities. This script runs under `set -eu`, so a single refusal
# used to abort the whole schema apply -- a user on a managed tier could not
# get a schema at all. Cluster settings are now attempted one capability-group
# at a time; a refusal is LOUD and names exactly what is lost, but it does not
# take the schema down with it. Migrations themselves stay fatal.
#
# POSTMORTEM_BOOTSTRAP_STRICT=1 (default, and what both compose files pin)
# keeps the old fail-closed behaviour for local/docker/CI: on the local root
# cluster a refused cluster setting means something is genuinely wrong, and
# C-SPANN recall / changefeed consolidation / the charter R10 (threat T7) audit
# controls must not silently degrade. Set 0 ONLY against a managed tier that
# withholds MODIFYCLUSTERSETTING. Fail-closed parse: anything not exactly "0"
# means strict.
BOOTSTRAP_STRICT=1
if [ "${POSTMORTEM_BOOTSTRAP_STRICT:-1}" = "0" ]; then
  BOOTSTRAP_STRICT=0
fi
BOOTSTRAP_DEGRADED=0
BOOTSTRAP_DEGRADED_REPORT=""
echo "bootstrap strict mode: $BOOTSTRAP_STRICT"

# Best-effort read of a setting's effective value, so a degraded message says
# what the cluster ACTUALLY has instead of guessing (charter R1/R6: report what
# was observed, never a plausible-looking stand-in). SHOW CLUSTER SETTING needs
# VIEWCLUSTERSETTING, which some managed tiers also withhold -- when the read is
# refused we say so. Never fatal: the command is the condition of an `if`, an
# explicitly-tested context that `set -e` does not act on. The command
# substitution is deliberately NOT piped: a pipeline's status is the LAST
# command's, so `cockroach ... | tail -n 1` would report success even when
# cockroach failed.
show_cluster_setting() {
  setting_raw=""
  if setting_raw=$(
    "$COCKROACH_BIN" sql --url "$DATABASE_URL" --format tsv \
      --execute "SHOW CLUSTER SETTING $1;" 2>/dev/null
  ); then
    echo "effective value of $1: [$(printf '%s\n' "$setting_raw" | tail -n 1)]"
  else
    echo "effective value of $1: UNREADABLE (SHOW CLUSTER SETTING refused too)"
  fi
}

# Applies one cluster-setting bootstrap file. On refusal cockroach's own error
# text is already on stderr; we add the capability that is now missing, the
# observed effective value, and the manual remediation. MUST be called as a
# plain statement, never as an `if` condition -- a function invoked from a
# tested context stops honouring `set -e` for its whole body.
apply_cluster_settings() {
  settings_file=$1
  capability=$2
  consequence=$3
  probe_setting=$4

  echo "bootstrap $settings_file"
  if "$COCKROACH_BIN" sql \
    --url "$DATABASE_URL" \
    --file "$DB_ROOT/bootstrap/$settings_file"
  then
    return 0
  fi

  echo "" >&2
  echo "!! =============== CLUSTER SETTING REFUSED ===============" >&2
  echo "!! file:            db/bootstrap/$settings_file" >&2
  echo "!! CAPABILITY LOST: $capability" >&2
  echo "!! CONSEQUENCE:     $consequence" >&2
  echo "!! $(show_cluster_setting "$probe_setting")" >&2
  echo "!! remediation:     run db/bootstrap/$settings_file as a cluster admin" >&2
  echo "!! =======================================================" >&2

  if [ "$BOOTSTRAP_STRICT" = "1" ]; then
    echo "!! POSTMORTEM_BOOTSTRAP_STRICT=1 (default): aborting the schema apply." >&2
    echo "!! Re-run with POSTMORTEM_BOOTSTRAP_STRICT=0 only on a managed tier" >&2
    echo "!! that withholds MODIFYCLUSTERSETTING." >&2
    exit 1
  fi

  BOOTSTRAP_DEGRADED=$((BOOTSTRAP_DEGRADED + 1))
  BOOTSTRAP_DEGRADED_REPORT="${BOOTSTRAP_DEGRADED_REPORT}BOOTSTRAP_DEGRADED capability=[$capability] file=[$settings_file] consequence=[$consequence]
"
  echo "!! POSTMORTEM_BOOTSTRAP_STRICT=0: continuing; migrations are unaffected." >&2
  return 0
}

# Pre-migration bootstrap: 0003_memory_indexes.sql's CREATE VECTOR INDEX needs
# this, so it must stay ahead of the migration chain. A refusal here is NOT
# silently swallowed: if the cluster genuinely has vector indexes disabled,
# migration 0003 below still fails fatally, which is the correct outcome.
apply_cluster_settings \
  001_enable_cspann.sql \
  "C-SPANN vector index (feature.vector_index.enabled)" \
  "unless this cluster already has it enabled, CREATE VECTOR INDEX in migration 0003_memory_indexes.sql will FAIL and the schema apply stops there -- vector recall is the core demo" \
  feature.vector_index.enabled

# The first migration creates the database that owns the migration ledger.
"$COCKROACH_BIN" sql \
  --url "$DATABASE_URL" \
  --file "$DB_ROOT/migrations/0001_create_database.sql"

"$COCKROACH_BIN" sql \
  --url "$DATABASE_URL" \
  --database postmortem \
  --execute "
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version STRING PRIMARY KEY,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    INSERT INTO schema_migrations (version)
    VALUES ('0001_create_database')
    ON CONFLICT (version) DO NOTHING;
  "

for migration in "$DB_ROOT"/migrations/000[2-9]*.sql; do
  filename=${migration##*/}
  version=${filename%.sql}
  # Capture first, `tail` second (audit B3-adjacent): a pipeline's exit status
  # is the LAST command's, so `cockroach ... | tail -n 1` hid a failed ledger
  # query from `set -e` and yielded an empty `existing` -- silently re-applying
  # a migration that was already recorded.
  ledger_rows=$(
    "$COCKROACH_BIN" sql \
      --url "$DATABASE_URL" \
      --database postmortem \
      --format tsv \
      --execute "SELECT version FROM schema_migrations WHERE version = '$version';"
  )
  existing=$(printf '%s\n' "$ledger_rows" | tail -n 1)

  if [ "$existing" = "$version" ]; then
    echo "skip $version (already applied)"
    continue
  fi

  echo "apply $version"
  "$COCKROACH_BIN" sql \
    --url "$DATABASE_URL" \
    --database postmortem \
    --file "$migration"
  "$COCKROACH_BIN" sql \
    --url "$DATABASE_URL" \
    --database postmortem \
    --execute "INSERT INTO schema_migrations (version) VALUES ('$version');"
done

# Post-migration bootstrap: cluster settings that depend on objects created by
# the migration chain (sql.log.user_audit references the postmortem_writer role
# from 0007). Split one file per capability group (audit B3) so a refusal in one
# group cannot swallow the other: `cockroach sql --file` stops at the first
# error in non-interactive mode, so grouping them would silently drop the
# remaining settings. Kept out of the migration chain on purpose -- see
# db/tests/test_migrations.py.
apply_cluster_settings \
  090_changefeed_settings.sql \
  "changefeeds (kv.rangefeed.enabled)" \
  "CREATE CHANGEFEED fails -- the sleep-time consolidation pipeline (changefeed -> webhook/SQS -> Lambda) is dead" \
  kv.rangefeed.enabled

apply_cluster_settings \
  091_audit_settings.sql \
  "role-based + admin SQL audit logging (sql.log.user_audit, sql.log.admin_audit.enabled)" \
  "the charter R10 / threat T7 audit-logging control is NOT in force on this cluster; only 0007's table-level EXPERIMENTAL_AUDIT trail remains (see docs/HARDENING.md 3.6)" \
  sql.log.user_audit

if [ "$BOOTSTRAP_DEGRADED" -gt 0 ]; then
  echo "" >&2
  echo "!! SCHEMA APPLIED, CLUSTER SETTINGS DEGRADED ($BOOTSTRAP_DEGRADED group(s)):" >&2
  printf '%s' "$BOOTSTRAP_DEGRADED_REPORT" >&2
  echo "!! Apply the listed files as a cluster administrator to restore them." >&2
  echo "!! Until then, do NOT claim the listed capabilities are in force." >&2
fi
