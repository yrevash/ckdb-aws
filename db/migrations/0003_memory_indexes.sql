-- C-SPANN indexes must be created while the memory tables are empty.
-- Apply db/bootstrap/001_enable_cspann.sql first as a cluster administrator.

USE postmortem;

CREATE VECTOR INDEX episodic_embedding_idx
    ON episodic_events (org_id, agent_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX semantic_embedding_idx
    ON semantic_facts (org_id, agent_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX procedural_embedding_idx
    ON procedural_memory (org_id, status, embedding vector_cosine_ops);

CREATE INVERTED INDEX IF NOT EXISTS episodic_metadata_idx
    ON episodic_events (metadata);

CREATE INDEX IF NOT EXISTS episodic_by_incident
    ON episodic_events (org_id, incident_id, occurred_at DESC)
    STORING (event_type, content, service_id, runbook_id);

CREATE INDEX IF NOT EXISTS semantic_current
    ON semantic_facts (org_id, subject, predicate)
    STORING (object, confidence, source, recorded_at)
    WHERE valid_to IS NULL;

CREATE INVERTED INDEX IF NOT EXISTS procedural_preconditions_idx
    ON procedural_memory (preconditions);

CREATE INDEX IF NOT EXISTS procedural_active
    ON procedural_memory (org_id, name)
    STORING (runbook_id, version, success_rate)
    WHERE status = 'active';
