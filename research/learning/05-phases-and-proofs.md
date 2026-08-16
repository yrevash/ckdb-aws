# 05 — The phases & the proofs (what's done, with numbers)

The build was structured in **phases**, each with an **exit gate** (a definition of "done") and a
**verifier script** that reproduces the proof. Phases 1–3 are complete and green; Phase 4 is the
remaining work.

## Phase 1 — Foundations & the spine ✅
**Goal:** one incident handled end-to-end using a seeded prior memory.
**Delivered:** the core schema (memory + operational, co-located), the deterministic simulator, the
responder loop, the read/write split (MCP recall + SQL writer), and the **one-transaction
`remediate_and_record`**. The console shell renders the recall/action/transaction/record events.
**Proof:** `scripts/verify_phase1.sh` — inject an alert → recall a seeded incident → grounded action →
commit action + memory together → render it.

## Phase 2 — Memory becomes load-bearing ✅
**Goal:** on the *same* incident stream, the memory-enabled agent must measurably beat a cold-start
agent.
**Delivered:** full three-stage procedural recall + safety/provenance gates; the changefeed→consolidation
pipeline; a **10-family** incident corpus with recurrence, near-miss, and novel variants; and the A/B
evaluation harness.
**Proof (honest — from `evaluation/reports/phase2.json`, schema v2). Per the
[Reality Charter](../../docs/reality/00-reality-charter.md), only real measured numbers appear here;
decision-quality numbers that need the real reasoning agent are marked pending, not estimated.**

| Metric | Value | Status |
|--------|-------|--------|
| **Retrieval** recall@1 (with 9 hard negatives) | **0.85** | ✅ measured — a close-but-wrong prior case can outrank gold, which is *correct* |
| Retrieval recall@10 / nDCG@10 | **1.0 / 0.94** | ✅ measured |
| Near-miss / novel abstention | correctly refused / escalated | ✅ measured |
| **Agent decision quality** (MTTR, wrong-actions, orders) | **pending real-agent run** | ⏳ needs the real Bedrock agent — a *competent* memoryless baseline ties on the deterministic sim, so no "% faster" is claimed yet |

> Earlier drafts showed "−63.6% MTTR / recall@10=1.0 / 40 orders avoided." The audit found those were
> **baked in** (answer-key corpus + a deliberately-dumb baseline + a hardcoded learning curve). They've
> been **removed**. The real MTTR delta will be measured when the real agent runs (Aug 1).

## Phase 3 — Resilience, temporal reasoning, hardening ✅
Three tracks, all integrated and green under `scripts/verify_phase3.sh`:

- **Track A — Resilience (real failover).** A **9-node, 3-region** cluster with `SURVIVE REGION FAILURE`.
  Before each kill, leaseholders for the probed tables are **pinned into the region we then kill**
  (verified via `SHOW RANGES`), so a genuine failover is exercised — the probe *fails* if no real
  lease handoff occurs. Measured: **RPO = 0 rows lost, content-verified during the outage**;
  **RTO = 3.1–4.9s** (target <10s; one run included a real serialization-retry during handoff). The
  earlier "0.045–0.099s" was a fake — it never killed a leaseholder. (`phase3-resilience.json`.)
- **Track B — Bitemporal & temporal drift.** Facts evolve as transitions, not overwrites; 2 drift
  families where an old fix becomes wrong. Temporal-validity is now checked against an **independent**
  oracle (not the responder's own predicate): the agent applies the *currently-valid* fix,
  stale-fact applications = 0.
- **Track C — Hardening.** Audit logging (proven live — even *denied* writes are audited), a
  **BACKUP → corrupt → point-in-time RESTORE** proof, and least-privilege reader/writer/consolidator
  roles. Findings in `docs/HARDENING.md`.

## Phase 3.5 — Enterprise security layer ✅ (added on top)
A full security posture (see learning file 06): a charter, AWS infra hardening (25 synth tests: no-wildcard
IAM, KMS everywhere, private VPC + PrivateLink, WAF, Bedrock Guardrails, CloudTrail/GuardDuty/Config),
agent+app guardrails (allowlist, provenance, injection defense, HMAC webhook, role-scoping, web headers),
and governance docs (threat model, controls matrix mapped to WA/CIS/NIST/EU-AI-Act/SOC2). All green.

## Phase 4 — Submission ⏳ (in progress — see file 09)
**Done:** the public-repo polish — README with tool-usage writeups, MIT LICENSE, architecture diagram,
the record-ready demo script, submission checklist.
**Not done (needs real AWS, Aug 1):** deploy to a live AWS account (Bedrock, ECS/Fargate, the
Lambda pipeline), the public demo URL, and recording the <3-minute video (including the real
region-failover capture).

## How "done" is proven (the three verifiers)
Each phase's verifier brings up the infrastructure it owns and runs that phase's suites:
- `scripts/verify_phase1.sh` — the vertical slice.
- `scripts/verify_phase2.sh` — memory load-bearing + *all* the app suites (backend, db, simulator,
  evaluation, consolidation, infra, web) + the security tests. **This is the main regression gate.**
- `scripts/verify_phase3.sh` — the 9-node region-kill + bitemporal + hardening.

If all three are green (they are), the local proof is complete. File 07 shows you how to run them.
