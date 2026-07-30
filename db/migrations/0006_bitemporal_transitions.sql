-- Phase 3 / Track B: bitemporal semantic-fact transitions.
--
-- The valid-time/system-time columns and the "currently valid" partial index
-- already exist:
--   * semantic_facts.valid_from / valid_to  (business time, 0002_core_schema.sql)
--   * semantic_facts.recorded_at            (system time,   0002_core_schema.sql)
--   * semantic_facts.superseded_by          (supersession,  0002_core_schema.sql)
--   * semantic_current (org_id, subject, predicate) WHERE valid_to IS NULL
--     (0003_memory_indexes.sql)
--   * semantic_recall_validity (org_id, agent_id, valid_from DESC)
--     (0005_recall_provenance_and_scope.sql)
-- This migration does NOT redefine any of the above. It adds only what Track
-- B's recall/audit surface is missing: a covering index for full belief
-- history by (subject, predicate), a convenience view mirroring the
-- "currently valid" index for ad-hoc/console queries, and the invariant that
-- a superseded fact is never left "open" (facts evolve as transitions, never
-- silent overwrites).
--
-- The atomic bitemporal transition itself (close the old fact, insert the
-- new one, set superseded_by) is DML, not DDL, so it lives in application
-- code: backend/src/postmortem_backend/adapters/recall.py
-- (INSERT_TRANSITIONED_FACT_SQL, CLOSE_SUPERSEDED_FACT_SQL). It is proven
-- against a live cluster to be two statements in one explicit,
-- client-retried transaction rather than the single implicit CTE statement
-- research/postmortem/01-memory-architecture.md §2 sketches -- CockroachDB
-- rejects an UPDATE and an INSERT against the same table in one statement by
-- default (sql.multiple_modifications_of_table.enabled=false, "to prevent
-- data corruption"), and this track does not flip that guard cluster-wide
-- for one query. Both statements still commit or roll back together: the
-- atomicity guarantee (never overwrite in place) is unchanged, only the
-- round-trip count.

USE postmortem;

-- Full belief-history lookups ("what did we believe about X, in order") are
-- already efficient primary-key range scans (semantic_facts' PK leads with
-- (org_id, subject, predicate)), but every non-key column -- object,
-- confidence, valid_from/valid_to, recorded_at, source, superseded_by --
-- still requires a table lookup per row without a covering index. This index
-- makes the belief-history query (db/queries/recall_semantic.sql's sibling
-- "point-in-time"/"history" shape) fully index-only.
CREATE INDEX IF NOT EXISTS semantic_facts_history
    ON semantic_facts (org_id, subject, predicate, recorded_at DESC)
    STORING (
        fact_id, object, confidence, valid_from, valid_to, source,
        superseded_by, agent_id
    );

-- A fact that has been superseded must have been closed first -- supersession
-- is the *result* of a transition, never a substitute for closing valid_to.
-- This is the schema-level guarantee behind "facts evolve as transitions,
-- not overwrites": there is never a row that is simultaneously "currently
-- believed" (valid_to IS NULL) and "replaced by something newer"
-- (superseded_by IS NOT NULL).
ALTER TABLE semantic_facts
    ADD CONSTRAINT semantic_superseded_implies_closed
    CHECK (superseded_by IS NULL OR valid_to IS NOT NULL);

-- Convenience view for the console/audit surface and ad-hoc `cockroach sql`
-- inspection: "what does the agent currently believe" without repeating the
-- WHERE valid_to IS NULL predicate everywhere. Backed by the semantic_current
-- partial index from 0003_memory_indexes.sql -- this view does not introduce
-- a second index or a second source of truth.
CREATE VIEW IF NOT EXISTS semantic_facts_current AS
    SELECT
        fact_id, org_id, agent_id, subject, predicate, object, confidence,
        source, valid_from, recorded_at
    FROM semantic_facts
    WHERE valid_to IS NULL;

-- Convenience view for the "why did the agent think X" explainability panel:
-- every fact this org/subject/predicate has ever held, oldest first, with
-- enough columns to render a transition timeline (superseded_by chains a
-- fact to whatever replaced it).
CREATE VIEW IF NOT EXISTS semantic_facts_belief_history AS
    SELECT
        fact_id, org_id, agent_id, subject, predicate, object, confidence,
        source, valid_from, valid_to, recorded_at, superseded_by
    FROM semantic_facts
    ORDER BY subject, predicate, recorded_at;
