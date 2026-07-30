#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_DATABASE_URL="${POSTMORTEM_TEST_DATABASE_URL:-postgresql://root@localhost:26257/postmortem?sslmode=disable}"

docker compose --project-directory "$ROOT_DIR" up -d cockroach
docker compose --project-directory "$ROOT_DIR" run --rm db-migrate

(
  cd "$ROOT_DIR/backend"
  POSTMORTEM_TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/pytest -q
)

(
  cd "$ROOT_DIR"
  POSTMORTEM_TEST_DATABASE_URL="$TEST_DATABASE_URL" \
    PYTHONPATH="$ROOT_DIR/backend/src:$ROOT_DIR/simulator:$ROOT_DIR/consolidation:$ROOT_DIR/evaluation" \
    backend/.venv/bin/pytest -q integration/tests
)

(
  cd "$ROOT_DIR/db"
  python3 -m unittest discover -s tests -v
)

(
  cd "$ROOT_DIR"
  PYTHONPATH="$ROOT_DIR/simulator:$ROOT_DIR/evaluation" \
    backend/.venv/bin/pytest -q simulator/tests evaluation/tests
  PYTHONPATH="$ROOT_DIR/simulator:$ROOT_DIR/evaluation" \
    backend/.venv/bin/python -m postmortem_eval --output evaluation/reports/phase2.json
  mkdir -p web/public
  cp evaluation/reports/phase2.json web/public/phase2-evaluation.json
)

(
  cd "$ROOT_DIR/consolidation"
  POSTMORTEM_TEST_DATABASE_URL="$TEST_DATABASE_URL" \
    ../backend/.venv/bin/pytest -q
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

echo "Phase 2 verification passed."
