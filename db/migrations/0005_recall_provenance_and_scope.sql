-- Phase 2 retrieval support: typed provenance and structured post-ANN filters.
-- Vector indexes remain the C-SPANN indexes created in 0003. These secondary
-- indexes support the temporal/service/provenance gates applied after ANN fetch.

USE postmortem;

CREATE TABLE IF NOT EXISTS semantic_fact_provenance (
    org_id             UUID        NOT NULL REFERENCES organizations (org_id),
    fact_id            UUID        NOT NULL REFERENCES semantic_facts (fact_id),
    incident_id        UUID        NOT NULL REFERENCES incidents (incident_id),
    episodic_event_id  UUID        REFERENCES episodic_events (event_id) ON DELETE SET NULL,
    role                STRING      NOT NULL
        CHECK (role IN ('source', 'reinforcement', 'counterexample')),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, fact_id, incident_id, recorded_at)
);

CREATE INDEX IF NOT EXISTS episodic_recall_scope
    ON episodic_events (org_id, agent_id, service_id, occurred_at DESC)
    STORING (event_id, incident_id, runbook_id, event_type, importance);

CREATE INDEX IF NOT EXISTS semantic_recall_validity
    ON semantic_facts (org_id, agent_id, valid_from DESC)
    STORING (fact_id, valid_to, confidence, subject, predicate, recorded_at);

CREATE INDEX IF NOT EXISTS procedural_recall_scope
    ON procedural_memory (org_id, agent_id, status, success_rate DESC)
    STORING (
        runbook_id, name, version, usage_count, last_used_at,
        applicable_service_tags, applicable_error_signatures
    );

CREATE INDEX IF NOT EXISTS runbook_provenance_by_runbook
    ON runbook_provenance (runbook_id, role, recorded_at DESC)
    STORING (incident_id, episodic_event_id);

CREATE INDEX IF NOT EXISTS semantic_provenance_by_fact
    ON semantic_fact_provenance (org_id, fact_id, role, recorded_at DESC)
    STORING (incident_id, episodic_event_id);

