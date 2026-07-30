-- Postmortem cluster bootstrap (CockroachDB v25.3+).
--
-- Run this once as a cluster administrator before applying migrations.
-- It is intentionally not part of the application migration chain because
-- the application migration identity must not have CLUSTER SETTING privilege.

SET CLUSTER SETTING feature.vector_index.enabled = true;
