-- $1 org_id, $2 agent_id, $3 service_id, $4 current incident_id,
-- $5 as_of, $6 query VECTOR(1024), $7 candidate limit.
-- Prefix equality on (org_id, agent_id) keeps the ANN walk C-SPANN-compatible.

WITH nearest AS (
    SELECT
        event_id, org_id, agent_id, incident_id, service_id, occurred_at,
        content, metadata, runbook_id, created_at,
        1 - (embedding <=> $6::VECTOR(1024)) AS similarity
    FROM episodic_events
    WHERE org_id = $1
      AND agent_id = $2
      AND embedding IS NOT NULL
      AND occurred_at <= $5
      AND (incident_id IS NULL OR incident_id != $4)
    ORDER BY embedding <=> $6::VECTOR(1024)
    LIMIT $7
)
SELECT
    nearest.*,
    nearest.incident_id AS source_case_id,
    action.action_id,
    action.action_type AS successful_action,
    action.outcome,
    action.transaction_id
FROM nearest
LEFT JOIN LATERAL (
    SELECT action_id, action_type, outcome, transaction_id
    FROM remediation_actions
    WHERE org_id = $1 AND memory_ref = nearest.event_id
    ORDER BY applied_at DESC
    LIMIT 1
) AS action ON true
WHERE nearest.service_id = $3 OR nearest.service_id IS NULL;

