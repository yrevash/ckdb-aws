-- Importance-weighted episodic retention. Consolidation raises importance on
-- events that contribute to durable facts/runbooks before this TTL can expire.

USE postmortem;

ALTER TABLE episodic_events SET (
    ttl_expiration_expression =
      'CASE
         WHEN importance >= 0.8 THEN occurred_at + INTERVAL ''365 days''
         WHEN importance >= 0.4 THEN occurred_at + INTERVAL ''90 days''
         ELSE occurred_at + INTERVAL ''30 days''
       END',
    ttl_job_cron = '@daily'
);
