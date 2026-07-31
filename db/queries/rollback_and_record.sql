-- Parameters:
--   $1 org_id, $2 agent_id, $3 incident_id, $4 session_id,
--   $5 service_id, $6 target_version, $7 runbook_id,
--   $8 embedding VECTOR(1024), $9 idempotency_key.
--
-- This single statement is the Phase 1 proof of the co-location contract:
-- rollback state, incident state, procedural usage, action provenance, and
-- episodic memory either all commit or all roll back.

WITH action_context AS (
    SELECT
        svc.service_id,
        inc.incident_id,
        rb.runbook_id
    FROM services AS svc
    JOIN incidents AS inc
      ON inc.org_id = svc.org_id
     AND inc.incident_id = $3
     AND inc.service_id = svc.service_id
    JOIN procedural_memory AS rb
      ON rb.org_id = svc.org_id
     AND rb.runbook_id = $7
    WHERE svc.org_id = $1
      AND svc.service_id = $5
      AND inc.status IN ('open', 'mitigating')
      AND rb.status = 'active'
),
rollback_deploy AS (
    INSERT INTO deploys (
        org_id, service_id, version, action, deployed_by, status, change_summary
    )
    SELECT
        $1, $5, $6, 'rollback', 'agent:postmortem', 'completed',
        'Automated rollback selected from procedural memory'
    FROM action_context
    RETURNING deploy_id
),
update_service AS (
    UPDATE services
       SET health = 'recovering',
           current_version = $6,
           current_deploy_id = (SELECT deploy_id FROM rollback_deploy),
           updated_at = now()
     WHERE org_id = $1
       AND service_id = $5
       AND EXISTS (SELECT 1 FROM rollback_deploy)
    RETURNING service_id
),
update_incident AS (
    UPDATE incidents
       SET status = 'mitigating', runbook_id = $7
     WHERE org_id = $1
       AND incident_id = $3
       AND EXISTS (SELECT 1 FROM rollback_deploy)
    RETURNING incident_id
),
touch_runbook AS (
    UPDATE procedural_memory
       SET usage_count = usage_count + 1,
           last_used_at = now()
     WHERE runbook_id = $7
       AND EXISTS (SELECT 1 FROM rollback_deploy)
    RETURNING runbook_id
),
record_episode AS (
    INSERT INTO episodic_events (
        org_id, agent_id, incident_id, session_id, service_id,
        event_type, content, metadata, runbook_id, importance, embedding
    )
    SELECT
        $1, $2, $3, $4, $5, 'action',
        'Rolled back service to ' || $6 || ' using runbook ' || $7::STRING,
        jsonb_build_object(
            'deploy_id', (SELECT deploy_id FROM rollback_deploy),
            'target_version', $6
        ),
        $7, 0.9, $8::VECTOR(1024)
    FROM update_service
    CROSS JOIN update_incident
    CROSS JOIN touch_runbook
    RETURNING event_id
),
record_action AS (
    INSERT INTO remediation_actions (
        org_id, incident_id, action_type, target_id, params, applied_by,
        outcome, memory_ref, idempotency_key
    )
    SELECT
        $1, $3, 'rollback_deploy', $5,
        jsonb_build_object('target_version', $6),
        'agent:postmortem', 'success',
        record_episode.event_id, $9
    FROM record_episode
    RETURNING action_id, transaction_id
)
SELECT
    rollback_deploy.deploy_id AS deploy_id,
    record_episode.event_id AS memory_id,
    record_action.action_id AS action_id,
    record_action.transaction_id AS transaction_id
FROM rollback_deploy
CROSS JOIN record_episode
CROSS JOIN record_action;
