# Phase 3 plan — resilience, temporal reasoning, and hardening

Executes Phase 3 of `IMPLEMENTATION_PHASES.md` / Week 3 of `research/postmortem/07-master-plan.md`
without rescoping them. Phases 1–2 are complete and green (`scripts/verify_phase2.sh`).

## Outcome

Prove the three wedge properties that only CockroachDB delivers, with **real telemetry**:
single-store atomicity, read-your-own-writes at global scale, and **RPO=0 region survival**. Add
bitemporal temporal reasoning (facts evolve as transitions, not overwrites) and production hardening
(audit, backups/PITR, Agent-Skills validation). The failover proof runs locally on a simulated
multi-region cluster now; the managed/self-hosted rehearsal for the video is Phase 4.

## Track A — Resilience & failover proof (local simulated multi-region)

1. A Docker Compose topology of a **9-node CockroachDB cluster** with `--locality region=…,zone=…`
   across **3 regions × 3 zones** (separate compose file/profile; distinct ports; does not disturb the
   Phase-1/2 single node).
2. Bootstrap the database with `SET PRIMARY REGION` + `ADD REGION` (×3) + `SURVIVE REGION FAILURE`;
   place the memory + operational tables so a full-region loss is survivable.
3. A **measurement harness** (`resilience/`) that produces machine-readable telemetry for:
   - **RPO=0**: committed-row count/checksum immediately before a region kill == after (zero loss).
   - **RTO**: wall-clock from region kill to restored write availability (target < 10 s).
   - **Read-your-own-writes**: staleness == 0 ms on the leaseholder path after commit.
   - **Atomicity**: `remediate_and_record` operational-mutation + episodic-write commit-or-abort together.
   - **Cross-agent visibility**: write via one gateway, read via another, no lag.
4. `scripts/failover_demo.sh` — kills a region's 3 nodes in a controlled, reproducible way and drives
   a live incident through the agent while the region is down (the money-shot rehearsal).
5. `scripts/verify_phase3.sh` — orchestrates the multi-region bring-up, the resilience harness, and
   the Track B/C suites; emits `evaluation/reports/phase3.json`.

## Track B — Bitemporal facts & temporal drift

1. Read the existing schema/recall first; add only what's missing.
2. Bitemporal semantic facts: `valid_from`/`valid_to` (business time) + `recorded_at` (system time) +
   supersession; a **single-statement atomic transition** that closes the old fact and opens the new
   one (no overwrite).
3. Recall must return the **currently-valid** fact for the incident's decision time (valid-time window
   filter), and expose superseded history for the UI/audit.
4. Simulator: **≥2 temporal-drift families** where a once-correct fix becomes wrong after an
   environment change, plus the corrected fix.
5. Evaluation: a **temporal-validity metric** (≥0.90) proving the memory arm applies the currently-
   valid fact, not a stale one; add to the scorecard + a drift learning view.

## Track C — Production hardening

1. **Audit logging**: enable SQL audit on the tables the agent mutates; a migration + a documented
   Cloud-org-audit plan; a verifiable local audit-trail check.
2. **Backups / PITR**: documented scheduled-backup + point-in-time-restore configuration and a local
   `BACKUP`/`RESTORE` smoke test (belt-and-suspenders against a buggy agent write, which replication
   cannot undo).
3. **CockroachDB Agent Skills**: run the relevant installed skills (production-readiness, privilege
   hardening, audit config, backup/DR posture) against the schema; capture findings + fixes in
   `docs/HARDENING.md`.
4. **Least-privilege**: confirm the reader vs writer service-account split is enforced at the SQL grant
   level for the agent's two paths.

## Track D — UI verification surfaces (sequenced after A & B)

1. Render the resilience telemetry (RPO=0 counter holding through the kill, RTO, freshness chip,
   atomicity envelope, cross-agent visibility) from `phase3.json`.
2. Render the temporal-drift story: a fact’s valid-time transition and the agent choosing the
   currently-valid fix.
3. Keep SSE live + deterministic replay as the camera-safe fallback.

## Phase 3 exit gate

- Simulated multi-region cluster comes up with `SURVIVE REGION FAILURE` and passes a scripted
  region-kill with **RPO=0** and **RTO < 10 s**, captured as real telemetry.
- `failover_demo.sh` drives a live incident through the agent during the region outage, three times,
  reproducibly.
- Bitemporal transition + temporal-validity ≥ 0.90 on the drift set; stale-fact application == 0.
- Audit trail, backup/PITR smoke test, and Agent-Skills findings recorded.
- Freshness == 0 ms, atomicity commit-or-abort, cross-agent lag == 0 verified.
- `scripts/verify_phase3.sh` reproduces the complete proof and emits `evaluation/reports/phase3.json`.
