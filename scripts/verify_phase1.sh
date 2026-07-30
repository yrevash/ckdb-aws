#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

docker compose --project-directory "$ROOT_DIR" up -d cockroach
docker compose --project-directory "$ROOT_DIR" run --rm db-migrate

(
  cd "$ROOT_DIR/backend"
  POSTMORTEM_TEST_DATABASE_URL="${POSTMORTEM_TEST_DATABASE_URL:-postgresql://root@localhost:26257/postmortem?sslmode=disable}" \
    .venv/bin/pytest -q
)

(
  cd "$ROOT_DIR"
  PYTHONPATH="$ROOT_DIR/backend/src:$ROOT_DIR/simulator" \
    backend/.venv/bin/pytest -q integration/tests
)

(
  cd "$ROOT_DIR/db"
  python3 -m unittest discover -s tests -v
)

(
  cd "$ROOT_DIR/simulator"
  PYTHONPATH=. python3 -m unittest discover -s tests -v
)

(
  cd "$ROOT_DIR/infra"
  .venv/bin/pytest -q
  .venv/bin/python app.py
)

(
  cd "$ROOT_DIR/web"
  pnpm test
  pnpm typecheck
  pnpm lint
  pnpm build
)

echo "Phase 1 verification passed."
