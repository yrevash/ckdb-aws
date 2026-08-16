-- Postmortem POST-migration cluster settings: SQL AUDIT LOGGING capability
-- group (CockroachDB v25.3+).
--
-- Run by db/apply.sh AFTER the migration chain, as a cluster administrator
-- (local dev `root`, or a one-time Cloud admin session), because:
--   * these are CLUSTER SETTINGs, which must NOT live in the app migration
--     chain (the migration identity has no MODIFYCLUSTERSETTING privilege, and
--     db/tests/test_migrations.py enforces that invariant), and
--   * `sql.log.user_audit` references the `postmortem_writer` role, which is
--     created by migration 0007_audit_logging.sql -- so this file must run
--     after 0007, not with the pre-migration bootstrap (001_enable_cspann.sql).
--
-- ONE CAPABILITY GROUP PER FILE (audit B3): `cockroach sql --file` stops at the
-- first error in non-interactive mode, so grouping unrelated settings would let
-- one refusal silently drop the rest. The two statements below are deliberately
-- kept TOGETHER because they are a single control -- charter R10 / threat T7
-- audit logging. If either is refused, that control is NOT in force on this
-- cluster and only mechanism 1 of docs/HARDENING.md 3.1 (the table-level
-- EXPERIMENTAL_AUDIT trail from 0007, a migration, always applied) remains.
-- apply.sh then reports
-- `CAPABILITY LOST: role-based + admin SQL audit logging` and (with
-- POSTMORTEM_BOOTSTRAP_STRICT=0) continues rather than killing the schema
-- apply. See docs/HARDENING.md 3.6.

-- Role-based audit logging: log every statement executed by the writer role
-- (the only role that can mutate agent-owned tables), cluster-wide. Pairs with
-- the table-level EXPERIMENTAL_AUDIT trail set in 0007. On live v26.2 the
-- per-role filter only accepts ALL/NONE (not READ/WRITE) -- see 0007's notes.
SET CLUSTER SETTING sql.log.user_audit = 'postmortem_writer ALL';

-- Admin audit logging: log operator-surface activity (schema migrations,
-- GRANT/REVOKE, cluster-setting changes). "Minimal overhead, high value" per
-- the configuring-audit-logging Agent Skill.
SET CLUSTER SETTING sql.log.admin_audit.enabled = true;
