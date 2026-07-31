-- Postmortem POST-migration cluster settings (CockroachDB v25.3+).
--
-- Run by db/apply.sh AFTER the migration chain, because:
--   * these are CLUSTER SETTINGs, which must NOT live in the app migration
--     chain (the migration identity has no MODIFYCLUSTERSETTING privilege, and
--     db/tests/test_migrations.py enforces that invariant), and
--   * `sql.log.user_audit` references the `postmortem_writer` role, which is
--     created by migration 0007_audit_logging.sql -- so this file must run
--     after 0007, not with the pre-migration bootstrap (001_enable_cspann.sql).
--
-- Apply as a cluster administrator (local dev `root`, or a one-time Cloud admin
-- session). See docs/HARDENING.md.

-- Changefeeds (the sleep-time consolidation pipeline: CockroachDB changefeed ->
-- webhook/SQS -> Lambda) require rangefeeds. Fresh v26.2 nodes ship with this
-- disabled; without it, CREATE CHANGEFEED fails. Flagged by the Track C
-- Agent-Skills review (docs/HARDENING.md).
SET CLUSTER SETTING kv.rangefeed.enabled = true;

-- Role-based audit logging: log every statement executed by the writer role
-- (the only role that can mutate agent-owned tables), cluster-wide. Pairs with
-- the table-level EXPERIMENTAL_AUDIT trail set in 0007. On live v26.2 the
-- per-role filter only accepts ALL/NONE (not READ/WRITE) -- see 0007's notes.
SET CLUSTER SETTING sql.log.user_audit = 'postmortem_writer ALL';

-- Admin audit logging: log operator-surface activity (schema migrations,
-- GRANT/REVOKE, cluster-setting changes). "Minimal overhead, high value" per
-- the configuring-audit-logging Agent Skill.
SET CLUSTER SETTING sql.log.admin_audit.enabled = true;
