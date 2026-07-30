-- Track C (production hardening): least-privilege reader/writer roles matching
-- the recall-vs-act split from research/postmortem/04-cockroachdb-deployment-
-- resilience.md §4.2, plus SQL audit logging on every table the agent's Act
-- path mutates.
--
-- Table set verified against the live code, not guessed: grep of
-- backend/src/postmortem_backend/adapters/cockroach.py (`remediate_and_record`,
-- the one-ACID-transaction wedge) and adapters/outcome.py (`record_outcome`)
-- on 2026-07-31 shows the agent's write path touches exactly: deploys,
-- services, incidents, episodic_events, remediation_actions, procedural_memory
-- (usage-count bump only). Nothing else is written by the live application
-- today -- see the "NOT granted" note below for what that implies.

USE postmortem;

-- ============================================================================
-- Section 1 -- least-privilege roles
-- ============================================================================

-- Defense-in-depth finding (confirmed live against a fresh v26.2 node via
-- `SHOW GRANTS ON DATABASE postmortem`): CockroachDB grants CREATE ON SCHEMA
-- <db>.public to the `public` pseudo-role by default. Every authenticated
-- principal -- including a hypothetical low-privilege reader -- can otherwise
-- create arbitrary objects in the application schema. Revoke it; only the
-- schema-owning migration identity needs DDL rights here.
REVOKE CREATE ON SCHEMA postmortem.public FROM public;

CREATE ROLE IF NOT EXISTS postmortem_reader;
CREATE ROLE IF NOT EXISTS postmortem_writer;

GRANT CONNECT ON DATABASE postmortem TO postmortem_reader, postmortem_writer;
GRANT USAGE ON SCHEMA postmortem.public TO postmortem_reader, postmortem_writer;

-- --- Reader: the agent's Recall path (doc 04 §4.2 item 1) -----------------
-- SELECT-only. Every table the recall/perceive path may legitimately query.
-- No INSERT/UPDATE/DELETE anywhere -- a compromised or buggy recall call
-- structurally cannot mutate operational or memory state.
GRANT SELECT ON TABLE
    organizations,
    services, deploys, service_dependencies, slos, metric_samples,
    incidents, alerts, orders, config_values,
    episodic_events, semantic_facts, procedural_memory,
    runbook_provenance, semantic_fact_provenance,
    session_turns, session_state,
    remediation_actions, agent_events, eval_probes
    TO postmortem_reader;

-- --- Writer: the agent's Act+Record path (doc 04 §4.2 item 2) -------------
-- Scoped to exactly the tables the atomic remediate_and_record / record_outcome
-- transactions touch -- nothing broader. SELECT is granted where the
-- transaction reads/joins/FOR UPDATEs/FK-checks a table; INSERT/UPDATE only
-- where a CTE in cockroach.py or outcome.py actually mutates it.
GRANT SELECT ON TABLE organizations TO postmortem_writer;                        -- FK check only
GRANT SELECT, UPDATE ON TABLE services TO postmortem_writer;                     -- health/version/deploy ptr
GRANT SELECT, INSERT ON TABLE deploys TO postmortem_writer;                      -- rollback/scale/restart record
GRANT SELECT, UPDATE ON TABLE incidents TO postmortem_writer;                    -- status/runbook_id/mttr/resolved_at
GRANT SELECT, INSERT ON TABLE episodic_events TO postmortem_writer;              -- the episodic write itself
GRANT SELECT, INSERT, UPDATE ON TABLE remediation_actions TO postmortem_writer;  -- action record + outcome update
GRANT SELECT, UPDATE ON TABLE procedural_memory TO postmortem_writer;            -- usage_count/last_used_at bump

-- Explicitly NOT granted to postmortem_writer: semantic_facts, session_turns,
-- session_state, agent_events, eval_probes, alerts, slos, metric_samples,
-- config_values, service_dependencies, orders, runbook_provenance,
-- semantic_fact_provenance. No code path in the live agent's write
-- transaction touches these today. In particular, semantic_facts/
-- procedural_memory INSERTs and *_provenance writes belong to the sleep-time
-- consolidation job (Track A/B's Lambda), which should get its OWN scoped
-- role rather than reuse postmortem_writer -- flagged as a follow-up in
-- docs/HARDENING.md for whoever wires up the changefeed consumer.

-- --- Concrete service-account principals, mirroring doc 04 §4.1/§4.2's
-- two-service-account model (postmortem-agent / postmortem-agent-writer) ---
-- Local/insecure clusters trust the connecting username with no password;
-- CockroachDB Cloud/production principals are minted via `ccloud service-
-- account create` + a SQL user WITH PASSWORD (rotated via Secrets Manager),
-- granted these same roles -- see docs/HARDENING.md.
CREATE USER IF NOT EXISTS postmortem_agent_reader;
CREATE USER IF NOT EXISTS postmortem_agent_writer;
GRANT postmortem_reader TO postmortem_agent_reader;
GRANT postmortem_writer TO postmortem_agent_writer;

-- ============================================================================
-- Section 2 -- SQL audit logging on the tables the agent mutates
-- ============================================================================

-- (a) Table-level audit trail (EXPERIMENTAL_AUDIT): every read+write against
-- these five tables is written to the SENSITIVE_ACCESS logging channel,
-- tagged with the executing SQL user -- answers "what did the agent write,
-- and when" straight from the log even if application-level logging is lost.
--
-- REAL GOTCHA found running this against a fresh v26.2 node: CockroachDB
-- v26.2 sets `schema_locked = true` on every new table by default (a
-- changefeed-performance optimization -- see 0002_core_schema.sql's tables,
-- all created with the default). EXPERIMENTAL_AUDIT is a descriptor mutation
-- and is unconditionally refused on a locked table:
--   ERROR: this schema change is disallowed because table "..." is locked
--   HINT: ALTER TABLE ... SET (schema_locked = false) ... then re-lock after.
-- Unlock, audit, relock, every time, or the ALTER fails outright.
ALTER TABLE incidents SET (schema_locked = false);
ALTER TABLE incidents EXPERIMENTAL_AUDIT SET READ WRITE;
ALTER TABLE incidents SET (schema_locked = true);

ALTER TABLE remediation_actions SET (schema_locked = false);
ALTER TABLE remediation_actions EXPERIMENTAL_AUDIT SET READ WRITE;
ALTER TABLE remediation_actions SET (schema_locked = true);

ALTER TABLE episodic_events SET (schema_locked = false);
ALTER TABLE episodic_events EXPERIMENTAL_AUDIT SET READ WRITE;
ALTER TABLE episodic_events SET (schema_locked = true);

ALTER TABLE semantic_facts SET (schema_locked = false);
ALTER TABLE semantic_facts EXPERIMENTAL_AUDIT SET READ WRITE;
ALTER TABLE semantic_facts SET (schema_locked = true);

ALTER TABLE procedural_memory SET (schema_locked = false);
ALTER TABLE procedural_memory EXPERIMENTAL_AUDIT SET READ WRITE;
ALTER TABLE procedural_memory SET (schema_locked = true);

-- (b) Role-based audit logging (`sql.log.user_audit`): the mechanism
-- CockroachDB has recommended since v22.2 (see the `configuring-audit-
-- logging` Agent Skill) -- logs every WRITE statement executed by the
-- postmortem_writer role, cluster-wide, with no table locking/schema-change
-- involved at all. Kept alongside (a) as a second, role-scoped signal: (a)
-- proves "this table was touched," (b) proves "this role touched it," and
-- together they cross-check each other. Admin-role activity (schema
-- migrations, GRANT/REVOKE) is covered separately by (c) below.
--
-- REQUIRES ADMIN / MODIFYCLUSTERSETTING: unlike every other statement in
-- this file (ordinary DDL/DCL the schema-owning migration identity already
-- needs), this is a cluster setting. db/bootstrap/001_enable_cspann.sql
-- established this project's convention that CLUSTER SETTING changes do NOT
-- belong to the routine migration identity's privilege set. It is included
-- here -- rather than a new bootstrap file, which is outside this task's file
-- ownership -- so that a single admin-privileged `cockroach sql --file` run
-- (local dev's `root`, or a one-time Cloud-console admin session) applies
-- everything atomically. If your migration identity lacks
-- MODIFYCLUSTERSETTING, run this one statement separately as an admin --
-- see docs/HARDENING.md, "Applying this migration in production."
--
-- REAL FINDING (contradicts the shipped configuring-audit-logging skill
-- example, which shows 'role READ'/'role WRITE' filters): on live v26.2,
-- sql.log.user_audit only accepts the statement filters ALL or NONE per
-- role -- `'postmortem_reader READ'` errors with
--   ERROR: unknown statement filter: "READ" (valid filters include: "ALL", "NONE")
-- There is no READ/WRITE-only granularity at the role-audit layer today.
-- postmortem_reader is intentionally left unaudited here (SQL grants already
-- make it structurally read-only, and read-path audit volume at any real
-- query rate is the "high overhead" case the skill itself warns against);
-- postmortem_writer -- the only role that can mutate agent-owned tables --
-- is fully audited.
SET CLUSTER SETTING sql.log.user_audit = 'postmortem_writer ALL';

-- (c) Admin audit logging: every statement executed by an `admin`-role
-- principal (schema migrations, GRANT/REVOKE, cluster-setting changes -- the
-- operator surface, not the agent's) is logged too. The
-- configuring-audit-logging skill calls this "minimal overhead, high value";
-- there is no reason to skip it once (b) above already pays the admin-
-- privilege cost for this migration run.
SET CLUSTER SETTING sql.log.admin_audit.enabled = true;
