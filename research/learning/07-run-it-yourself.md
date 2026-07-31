# 07 — Run it yourself (learn by doing)

The fastest way to *understand* Postmortem is to run the verifiers and watch the proofs. Everything here
works **locally, with no AWS credentials** (the backend uses a `fake` runtime; CockroachDB runs in
Docker).

## Prerequisites
- **Docker** (for CockroachDB)
- **Python 3.12+**
- **Node.js 22+** and **pnpm 10+**

One-time: install deps in `backend/`, `infra/` (Python venvs) and `web/` (`pnpm install`). Each folder's
README has the exact commands.

## 1. Start the database
```bash
docker compose up -d cockroach
docker compose run --rm db-migrate     # applies bootstrap + migrations
```

## 2. Run the three verifiers (the proofs)
```bash
# Phase 1 — the vertical slice: alert → recall → remediate_and_record (one txn) → events
./scripts/verify_phase1.sh

# Phase 2 — memory load-bearing + ALL suites (backend, db, sim, eval, consolidation, infra, web,
#           security). This is the main regression gate. It also writes the A/B scorecard.
./scripts/verify_phase2.sh

# Phase 3 — brings up the 9-node multi-region cluster, KILLS a region, measures RPO=0/RTO,
#           then runs the bitemporal + hardening proofs.
./scripts/verify_phase3.sh
```
Green output ("Phase N verification passed.") means the proof reproduced.

## 3. Read the numbers
After `verify_phase2.sh`, open **`evaluation/reports/phase2.json`** — the A/B scorecard. Look for:
- `comparison.median_mttr_reduction_percent` → **63.6**
- `recall.recall_at_10` → **1.0**
- `comparison.wrong_actions_avoided` → **20**
- `learning_curve` → MTTR dropping across occurrences.

After `verify_phase3.sh`, open **`evaluation/reports/phase3-resilience.json`** — look at `probes.rpo`
(0 rows lost) and `probes.rto` (seconds), and `node_liveness` (9 → 6 → 9 across the kill).

## 4. See the console (the UI)
```bash
cd web
pnpm build && pnpm start          # serves the console on http://localhost:3000
```
It runs on **mock/replay data**, so you get the full experience without a backend:
- **Investigation** tab — the incident + Recall Thread + Transaction Envelope.
- **Resilience** tab — the RPO=0 "failover theater."
- **Temporal drift** tab — facts evolving over time.

*(This is how we made the phone preview earlier — `pnpm start`, then a tunnel like `cloudflared`/`ngrok`
in front of port 3000.)*

## 5. Try the standalone security/hardening proofs
```bash
./scripts/audit_check.sh          # spins its own node; proves audit logging + least-privilege denies
./scripts/backup_pitr_smoke.sh    # BACKUP → simulate a bad write → point-in-time RESTORE recovers it
./scripts/failover_demo.sh        # the region-kill demo, standalone (RESILIENCE_TEARDOWN=1 to clean up)
```

## Where to look in the code while you run
- The wedge transaction: `db/queries/rollback_and_record.sql`.
- The recall logic: `backend/src/postmortem_backend/recall.py` + `adapters/recall.py`.
- The guardrails: `backend/src/postmortem_backend/guardrails/`.
- The simulator families: `simulator/postmortem_sim/conductor.py`.
- The eval logic: `evaluation/postmortem_eval/runner.py`.

## Cleanup
```bash
docker compose down
docker compose -f docker-compose.multiregion.yml down   # if you ran phase 3
```

> Tip: `verify_phase2.sh` is the one to run after any change — if it's green, you haven't broken
> anything. That's the same gate we used throughout the build.
