-- Backend/DB audit fixes DB#2 and DB#6.
--
-- DB#2: `semantic_current` (0003_memory_indexes.sql) was a plain (non-unique)
-- partial index on (org_id, subject, predicate) WHERE valid_to IS NULL. A
-- plain index does not stop two facts from both being "currently valid" for
-- the same (org_id, subject, predicate) at once -- e.g. two concurrent
-- bitemporal transitions (adapters/recall.py's transition_fact) racing to
-- open a *first* fact for a subject/predicate that has no current row yet.
-- Recreate it UNIQUE, mirroring `config_current`'s unique partial index on
-- config_values (0002_core_schema.sql) -- same shape, same "at most one open
-- row" contract, applied to semantic_facts. Same columns/STORING/WHERE as
-- before; only the uniqueness changes, so every existing consumer of the
-- index (SEMANTIC_RECALL_SQL, the semantic_facts_current view) is unaffected.
--
-- This is a genuine behavior change for adapters/recall.py's transition_fact:
-- with a true unique constraint, closing the old "current" row must commit
-- before the new row is inserted (inserting the new row first would
-- transiently leave two rows open for the same key and fail immediately).
-- See adapters/recall.py's CLOSE_SUPERSEDED_FACT_SQL / INSERT_TRANSITIONED_
-- FACT_SQL / LINK_SUPERSEDED_FACT_SQL ordering and its 23505 retry handling
-- for the corresponding application-side fix.
--
-- DB#6: recall.py's EPISODIC_RECALL_SQL LATERAL-joins remediation_actions on
-- (org_id, memory_ref) with no supporting index -- CockroachDB has no choice
-- but to scan the whole org's remediation_actions per candidate episode.
-- remediation_actions already has a PK on action_id and a
-- (org_id, incident_id, applied_at) index (remediation_by_incident), neither
-- of which serves an equality lookup on memory_ref. Add the missing index.

USE postmortem;

DROP INDEX IF EXISTS semantic_facts@semantic_current;

CREATE UNIQUE INDEX IF NOT EXISTS semantic_current
    ON semantic_facts (org_id, subject, predicate)
    STORING (object, confidence, source, recorded_at)
    WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS remediation_actions_by_memory_ref
    ON remediation_actions (org_id, memory_ref)
    STORING (action_id, action_type, outcome, transaction_id, applied_at);
