# Postmortem database

This directory owns the CockroachDB half of the Phase 1 spine: mutable
system-under-management state and persistent agent memory live in the same
`postmortem` database and therefore share one serializable transaction boundary.

## Apply order

Target CockroachDB v25.3 or newer (v26.2 preferred).

1. As a cluster administrator, run `bootstrap/001_enable_cspann.sql`.
2. Apply `migrations/*.sql` in lexical order.
3. Load fixtures only after `0003_memory_indexes.sql`; creating a C-SPANN index
   over a populated table can block writes during its backfill.

For local development and Docker Compose, the migration-ledger-backed runner is:

```sh
DATABASE_URL='postgresql://root@localhost:26257/defaultdb?sslmode=disable' db/apply.sh
```

The bootstrap setting is deliberately separate from migrations. The runtime
writer should not have cluster-administrator privileges.

## What is present

- Operational tables: organizations, services, dependencies, deploys, SLOs,
  metric samples, incidents, alerts, orders, bitemporal configuration, and
  remediation actions.
- Memory tables: episodic, bitemporal semantic, versioned procedural, minimal
  working/session memory, and runbook provenance.
- Instrumentation: typed agent events and system-property probes.
- C-SPANN cosine indexes over normalized `VECTOR(1024)` values, scoped by
  organization plus agent/status.
- Importance-weighted episodic retention.

`queries/rollback_and_record.sql` is the concrete one-statement transaction
contract for the responder implementation. It atomically updates the live SUM,
the incident, the runbook track record, action provenance, and the memory of the
action.

## Read consistency contract

Recall on the decide/act path uses normal strongly consistent reads. Do not use
follower reads there. Follower reads are reserved for timeline polling and
analytics where bounded staleness is acceptable.

## Phase 2 consolidation changefeed

`changefeeds/create_consolidation_changefeed.sql.example` is the deployment template for the
episodic-memory webhook feed. Render its API Gateway host and bearer secret outside version control,
then execute it with a principal holding `CHANGEFEED` on `episodic_events`. The feed emits updated
row envelopes plus resolved timestamps, retries delivery, and pauses on a terminal sink error so
operators can inspect and resume it without silently dropping consolidation input.

## Phase boundary

Multi-region locality, `SURVIVE REGION FAILURE`, production RBAC identities, and seed loading into a
deployed cluster belong to later infrastructure/integration phases. This schema keeps the required
keys and history so those phases do not need to redesign the core.
