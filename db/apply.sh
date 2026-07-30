#!/bin/sh

set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"

DB_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COCKROACH_BIN=${COCKROACH_BIN:-cockroach}

"$COCKROACH_BIN" sql \
  --url "$DATABASE_URL" \
  --file "$DB_ROOT/bootstrap/001_enable_cspann.sql"

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
  existing=$(
    "$COCKROACH_BIN" sql \
      --url "$DATABASE_URL" \
      --database postmortem \
      --format tsv \
      --execute "SELECT version FROM schema_migrations WHERE version = '$version';" |
      tail -n 1
  )

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
