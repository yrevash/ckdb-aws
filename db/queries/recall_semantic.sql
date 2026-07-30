-- $1 org_id, $2 agent_id, $3 as_of, $4 query VECTOR(1024),
-- $5 candidate limit, $6 affected service_id.
-- Only facts valid and already recorded at the incident's business time survive:
-- valid_from/valid_to is the bitemporal *valid-time* window (when the fact was
-- true in the SUM's world), recorded_at is *system time* (when Postmortem
-- learned it) -- both are gated against the incident's decision time so a
-- fact from the future, or a fact already superseded by then, never leaks in.
--
-- Facts are never overwritten in place (see db/migrations/0006 and
-- backend/src/postmortem_backend/adapters/recall.py's INSERT_TRANSITIONED_FACT_SQL
-- / CLOSE_SUPERSEDED_FACT_SQL pair): a transition inserts the new row, then
-- closes the old row (sets valid_to) and points its superseded_by at the new
-- fact_id, both in one atomic transaction. `predecessor` below
-- surfaces that prior belief (if any) for the console/audit "why did the
-- agent think X" view -- it is the fact whose superseded_by equals the
-- currently-returned fact's fact_id, i.e. what this fact replaced.

WITH nearest AS (
    SELECT
        fact_id, org_id, agent_id, subject, predicate, object, confidence,
        source, valid_from, valid_to, recorded_at, superseded_by,
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
    ), ARRAY[]::UUID[]) AS provenance_ids,
    (
        SELECT jsonb_build_object(
            'fact_id', predecessor.fact_id,
            'object', predecessor.object,
            'confidence', predecessor.confidence,
            'valid_from', predecessor.valid_from,
            'valid_to', predecessor.valid_to,
            'recorded_at', predecessor.recorded_at,
            'source', predecessor.source
        )
        FROM semantic_facts AS predecessor
        WHERE predecessor.org_id = nearest.org_id
          AND predecessor.superseded_by = nearest.fact_id
        LIMIT 1
    ) AS predecessor
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
