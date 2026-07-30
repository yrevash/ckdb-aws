# Postmortem implementation phases

This is the execution breakdown of `research/postmortem/07-master-plan.md`. It does not replace or
rescope the master plan.

## Phase 1 — Foundations and the spine

**Goal:** one incident handled end to end using a seeded prior memory.

**Status:** implemented and passing the repository-wide exit-gate verifier.

Deliverables:

- CockroachDB core memory and operational schema, with vector indexes created before seed data.
- Deterministic SUM conductor with two or three incident families.
- Strands-compatible responder boundary using Bedrock for reasoning/embedding.
- Managed MCP read adapter and separately scoped direct-SQL writer.
- `remediate_and_record` action and episodic write in one serializable transaction.
- Typed agent event stream and minimal HTTP/SSE API.
- Three-rail Next.js console shell wired to the event contract, initially replay-capable.
- AWS CDK skeleton for Fargate, secrets, Bedrock permissions, artifacts, and observability.
- Unit/integration verification of the successful and rollback paths.

Exit gate:

1. Inject a deterministic alert.
2. Recall a seeded matching incident.
3. Produce a grounded action proposal.
4. Commit the operational mutation and episodic record together.
5. Render the recall, action, transaction, and record events in the console.

## Phase 2 — Memory becomes load-bearing

- Full three-stage procedural recall and safety/provenance gates.
- Changefeed-to-SQS consolidation pipeline and Lambda write-back.
- Complete ten-family generated corpus and S3 fixture.
- With-memory versus cold-start A/B harness, recall@k, MTTR, and business-impact metrics.
- Recall Thread, similarity dial, Transaction Envelope, and live event wiring.

Exit gate: measurable MTTR improvement on the same seeded recurrence stream.

## Phase 3 — Resilience, temporal reasoning, and polish

- Multi-region CockroachDB configuration with `SURVIVE REGION FAILURE`.
- Bitemporal fact transitions and drift scenarios.
- PITR/backups, audit logging, hardening through CockroachDB Agent Skills.
- Failover rehearsal through the selected Tier A/B/C mechanism.
- RPO, RTO, freshness, atomicity, and cross-agent verification in the UI.

Exit gate: three successful dress rehearsals with real telemetry and a defensible RPO/RTO proof.

## Phase 4 — Submission

- Feature freeze and regression pass.
- Public AWS deployment and demo URL.
- README, architecture diagram, tool-usage evidence, setup instructions, and license visibility.
- Real failover capture, deterministic replay fallback, and sub-three-minute final video.
- Devpost submission completed with buffer before the deadline.
