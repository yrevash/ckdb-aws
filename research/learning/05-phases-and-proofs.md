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
**Proof (from `evaluation/reports/phase2.json`):**

| Metric | Cold-start | With memory | Result |
|--------|:---------:|:-----------:|--------|
| Recall@10 | — | **1.0** | (target ≥0.95) ✅ |
| Median MTTR | 660s | **240s** | **−63.6%** ✅ |
| Wrong actions | 20 | **0** | ✅ |
| Failed orders | 197 | 157 | **40 avoided** (~$2,576) ✅ |
| First-action accuracy | 31% | **100%** | ✅ |
| Near-miss authorization | — | **0** | (the red-herring is correctly refused) ✅ |

And it **compounds** across repeat occurrences — median MTTR **300 → 180 → 120s** by 1st/2nd/3rd time
the agent sees a failure family. That "learning curve" is the proof memory makes the agent *improve*,
not just *remember*.

## Phase 3 — Resilience, temporal reasoning, hardening ✅
Three tracks, all integrated and green under `scripts/verify_phase3.sh`:

- **Track A — Resilience.** A **9-node, 3-region** CockroachDB cluster (in Docker) with `SURVIVE REGION
  FAILURE`. A scripted **region-kill** with real telemetry: **RPO = 0 rows lost** (every run),
  **RTO = 0.045–0.099s** (target <10s), read-your-writes staleness **0ms**, cross-agent visibility
  **0 lag**, atomicity commit-or-abort. (`evaluation/reports/phase3-resilience.json`.)
- **Track B — Bitemporal & temporal drift.** Facts evolve as transitions, not overwrites; 2 drift
  families where an old fix becomes wrong. **temporal-validity = 1.0** (the agent applies the
  *currently-valid* fix, target ≥0.90), **stale-fact applications = 0.**
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
