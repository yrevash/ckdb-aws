# Phase 2 plan — memory becomes load-bearing

This plan executes Week 2 of `research/postmortem/07-master-plan.md` without changing its scope.

## Outcome

Postmortem must do more than store memory. On an identical deterministic recurrence stream, the
memory-enabled arm must retrieve the right prior evidence, select better actions, and produce lower
MTTR and business impact than the cold-start arm.

## Track A — three-stage recall and safe execution

1. Retrieve episodic, current semantic, and active procedural candidates using tenant-prefixed
   CockroachDB vector queries.
2. Filter by tenant/agent, service scope, tags, error signature, valid-time window, status,
   provenance, confidence, and minimum track record.
3. Rerank with a deterministic composite of similarity, scope match, freshness, and historical
   success; retain evidence diversity across memory types.
4. Expose the selected IDs, scores, rejection reasons, and runbook provenance to the responder and
   console.
5. Support an explicit cold-start mode that bypasses learned memory while leaving the incident,
   simulator seed, and action interface unchanged.
6. Execute only provenance-backed allowlisted steps; preserve Phase 1 approval and atomicity gates.

## Track B — sleep-time consolidation

1. Emit resolved incident/action/episode changes into a changefeed webhook.
2. Fast-ack through API Gateway/Lambda, enqueue to SQS, and isolate poison messages in a DLQ.
3. Group complete incident histories using an idempotency key based on the resolved incident window.
4. Distill reusable semantic facts and procedural runbooks through a Bedrock-compatible boundary.
5. Create a draft runbook only from successful, provenance-bearing episodes.
6. Reinforce successful procedures, weaken counterexamples, maintain success/usage statistics, and
   record every source incident in `runbook_provenance`.
7. Make the write immediately visible to responder recall in the same CockroachDB store.

## Track C — corpus and controlled evaluation

1. Expand the deterministic SUM to ten incident families with base, recurrence, near-miss, and
   novel/abstention variants.
2. Generate the two experiment arms from the same fixture, seed, schedule, and hidden oracle.
3. Cold arm: no learned organizational memory. Memory arm: episodic, semantic, and consolidated
   procedural recall.
4. Measure recall@5/@10, precision/nDCG where gold labels exist, first-action accuracy, abstention,
   median/p90 MTTR, wrong actions, escalations, failed orders, and token/cost proxy.
5. Emit a machine-readable scorecard plus the MTTR-by-occurrence learning curve.

## Track D — live evidence console

1. Keep SSE as the canonical live transport and deterministic replay as the camera-safe fallback.
2. Show all recalled memory types, their component scores, scope/freshness, and provenance.
3. Show why candidates were rejected and whether the runbook passed every safety gate.
4. Add a compact memory-vs-cold scorecard and MTTR-by-occurrence chart sourced from the evaluator.
5. Keep the Recall Thread and Transaction Envelope as the primary proof surfaces.

## Phase 2 exit gate

- Ten simulator families and their recurrence/novel variants pass deterministic replay tests.
- Live CockroachDB recall returns scoped episodic, semantic, and procedural evidence with no
  cross-tenant leakage.
- A successful consolidated runbook is visible to the responder immediately after commit.
- Failed or unproven memories cannot authorize an action.
- `recall@10 >= 0.95` on the labeled recurrence set.
- The memory arm has lower median MTTR, fewer wrong actions, and fewer failed orders than cold-start
  on the identical seeded stream.
- The console renders real recall evidence and the generated A/B scorecard.
- `scripts/verify_phase2.sh` reproduces the complete proof.
