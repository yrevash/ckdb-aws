-- Phase 3, Track A — convert `postmortem` into a 3-region database with
-- SURVIVE REGION FAILURE (RF=5, split 2+2+1 across regions), on the local
-- simulated multi-region cluster (docker-compose.multiregion.yml).
--
-- Apply AFTER db/apply.sh (migrations 0001-0005, plus bootstrap/001's
-- feature.vector_index.enabled) has already run against this cluster, and
-- only against a cluster whose 9 nodes were started with
-- `--locality=region=us-east-1|us-east-2|us-west-2,zone=a|b|c` (see
-- docker-compose.multiregion.yml). `SHOW REGIONS FROM CLUSTER` must already
-- list all three regions before this file is applied — that requires all 9
-- nodes to have joined, which is why scripts/failover_demo.sh runs `mr-init`
-- and waits for node liveness before this step.
--
-- Like bootstrap/001_enable_cspann.sql, this is intentionally NOT part of the
-- db/apply.sh migration ledger: it is a one-time cluster-topology decision,
-- not an application schema change, and the runtime writer identity should
-- not need the privileges this requires.
--
-- This does NOT touch or redefine the Phase 1/2 single-node cluster
-- (docker-compose.yml) — that cluster is never multi-region and this file is
-- never applied to it.

USE postmortem;

-- Regions must match the node locality flags in docker-compose.multiregion.yml.
-- us-east-1 is primary (matches research/postmortem/04-cockroachdb-deployment-
-- resilience.md); us-east-2 is the region scripts/failover_demo.sh kills.
ALTER DATABASE postmortem PRIMARY REGION "us-east-1";
ALTER DATABASE postmortem ADD REGION IF NOT EXISTS "us-east-2";
ALTER DATABASE postmortem ADD REGION IF NOT EXISTS "us-west-2";

-- The charter-mandated survival goal (wedge #3): region loss, not just zone
-- loss, must be survivable with zero data loss. This is the statement that
-- bumps every table in `postmortem` from RF=3 to RF=5 (2+2+1 across the three
-- regions) — verified live: CockroachDB automatically folds every
-- pre-existing table (created single-region by migrations 0001-0005) into the
-- new multi-region zone configuration the moment this statement completes;
-- `SHOW ZONE CONFIGURATION FOR TABLE episodic_events` afterward shows
-- num_replicas = 5, constraints = '{+region=us-east-1: 2, +region=us-east-2: 2,
-- +region=us-west-2: 1}'.
ALTER DATABASE postmortem SURVIVE REGION FAILURE;

-- Make the inherited locality explicit and re-assertable (idempotent no-op if
-- already set) on every memory + operational table, rather than relying
-- silently on the automatic conversion above. REGIONAL BY TABLE IN PRIMARY
-- REGION is the correct locality for all of these: none of them need
-- per-row/per-tenant homing (that is a `01-memory-architecture.md` decision,
-- out of Track A's scope) — they need the whole table to survive a region
-- loss, which REGIONAL BY TABLE's RF=5 zone config already provides. The
-- leaseholder stays pinned to the primary region (us-east-1) in steady state;
-- during the failover demo, killing a *non-primary* region (us-east-2) never
-- moves a leaseholder at all (the surviving replicas simply drop from 5 to 3,
-- still a quorum) — the harness measures write availability through that
-- event regardless.
ALTER TABLE organizations SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE services SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE deploys SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE service_dependencies SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE slos SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE metric_samples SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE incidents SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE alerts SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE orders SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE config_values SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;

ALTER TABLE episodic_events SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE semantic_facts SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE procedural_memory SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE runbook_provenance SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE semantic_fact_provenance SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE session_turns SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE session_state SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;

ALTER TABLE remediation_actions SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE agent_events SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;
ALTER TABLE eval_probes SET LOCALITY REGIONAL BY TABLE IN PRIMARY REGION;

-- Sanity output for the operator/CI log, not consumed programmatically.
SHOW REGIONS FROM DATABASE postmortem;
SHOW ZONE CONFIGURATION FOR TABLE episodic_events;
