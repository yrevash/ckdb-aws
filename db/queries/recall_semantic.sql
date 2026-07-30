-- $1 org_id, $2 agent_id, $3 as_of, $4 query VECTOR(1024),
-- $5 candidate limit, $6 affected service_id.
-- Only facts valid and already recorded at the incident's business time survive.

WITH nearest AS (
    SELECT
        fact_id, org_id, agent_id, subject, predicate, object, confidence,
        source, valid_from, valid_to, recorded_at,
        1 - (embedding <=> $4::VECTOR(1024)) AS similarity
    FROM semantic_facts
    WHERE org_id = $1
      AND agent_id = $2
      AND embedding IS NOT NULL
      AND valid_from <= $3
      AND (valid_to IS NULL OR valid_to > $3)
      AND recorded_at <= $3
    ORDER BY embedding <=> $4::VECTOR(1024)
    LIMIT $5
)
SELECT
    nearest.*,
    CASE WHEN nearest.subject LIKE 'service:%' THEN $6 ELSE NULL END
        AS scoped_service_id,
    COALESCE((
        SELECT array_agg(episodic_event_id)
        FROM semantic_fact_provenance
        WHERE org_id = nearest.org_id
          AND fact_id = nearest.fact_id
          AND episodic_event_id IS NOT NULL
          AND role IN ('source', 'reinforcement')
    ), ARRAY[]::UUID[]) AS provenance_ids
FROM nearest
WHERE nearest.subject LIKE 'org:%'
   OR nearest.subject IN (
       SELECT 'service:' || name
       FROM services
       WHERE org_id = $1 AND service_id = $6
       UNION ALL
       SELECT 'service:' || service_id::STRING
       FROM services
       WHERE org_id = $1 AND service_id = $6
   );
