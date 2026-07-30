-- $1 org_id, $2 agent_id, $3 as_of, $4 query VECTOR(1024),
-- $5 candidate limit.
-- The C-SPANN index prefix is (org_id, status); agent scoping is an additional
-- safety filter. Applicability and thresholds are enforced by the responder.

WITH nearest AS (
    SELECT
        runbook_id, org_id, agent_id, name, version, trigger_desc,
        applicable_service_tags, applicable_error_signatures, preconditions,
        steps, postconditions, usage_count, success_count, failure_count,
        success_rate, avg_resolution_seconds, last_used_at, created_by, created_at,
        1 - (embedding <=> $4::VECTOR(1024)) AS similarity
    FROM procedural_memory
    WHERE org_id = $1
      AND status = 'active'
      AND agent_id = $2
      AND embedding IS NOT NULL
      AND created_at <= $3
    ORDER BY embedding <=> $4::VECTOR(1024)
    LIMIT $5
)
SELECT
    nearest.*,
    COALESCE((
        SELECT count(*)
        FROM runbook_provenance
        WHERE runbook_id = nearest.runbook_id
          AND role IN ('source', 'reinforcement')
    ), 0) AS positive_provenance_count,
    COALESCE((
        SELECT count(*)
        FROM runbook_provenance
        WHERE runbook_id = nearest.runbook_id
          AND role = 'counterexample'
    ), 0) AS counterexample_count,
    COALESCE((
        SELECT array_agg(episodic_event_id)
        FROM runbook_provenance
        WHERE runbook_id = nearest.runbook_id
          AND role IN ('source', 'reinforcement')
          AND episodic_event_id IS NOT NULL
    ), ARRAY[]::UUID[]) AS provenance_ids
FROM nearest;

