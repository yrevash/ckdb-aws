-- Postmortem Phase 1: co-located operational state and agent memory.
-- Target: CockroachDB v25.3+ (v26.2 preferred).

USE postmortem;

CREATE TABLE IF NOT EXISTS organizations (
    org_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         STRING      NOT NULL UNIQUE,
    display_name STRING      NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- System under management: mutable operational state
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS services (
    service_id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 UUID        NOT NULL REFERENCES organizations (org_id),
    name                   STRING      NOT NULL,
    tier                   STRING      NOT NULL DEFAULT 'standard'
        CHECK (tier IN ('critical-path', 'standard')),
    owner_team             STRING,
    health                 STRING      NOT NULL DEFAULT 'healthy'
        CHECK (health IN ('healthy', 'degraded', 'down', 'recovering')),
    current_version        STRING      NOT NULL,
    previous_stable_version STRING,
    capacity_units         INT8        NOT NULL DEFAULT 1 CHECK (capacity_units > 0),
    current_deploy_id      UUID,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT services_org_name_key UNIQUE (org_id, name)
);

CREATE TABLE IF NOT EXISTS deploys (
    deploy_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID        NOT NULL REFERENCES organizations (org_id),
    service_id     UUID        NOT NULL REFERENCES services (service_id),
    version        STRING      NOT NULL,
    action         STRING      NOT NULL DEFAULT 'deploy'
        CHECK (action IN ('deploy', 'rollback', 'scale', 'restart')),
    change_summary STRING,
    deployed_by    STRING      NOT NULL,
    status         STRING      NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed', 'failed', 'rolled_back')),
    rolled_back    BOOL        NOT NULL DEFAULT false,
    deployed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE services
    ADD CONSTRAINT services_current_deploy_fk
    FOREIGN KEY (current_deploy_id) REFERENCES deploys (deploy_id);

CREATE TABLE IF NOT EXISTS service_dependencies (
    dependency_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID        NOT NULL REFERENCES organizations (org_id),
    from_service_id UUID        NOT NULL REFERENCES services (service_id),
    to_service_id   UUID        NOT NULL REFERENCES services (service_id),
    dependency_key  STRING      NOT NULL,
    dependency_type STRING      NOT NULL
        CHECK (dependency_type IN ('sync', 'async', 'datastore', 'cache', 'external')),
    criticality     STRING      NOT NULL DEFAULT 'required'
        CHECK (criticality IN ('required', 'degraded-ok', 'optional')),
    status          STRING      NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'degraded', 'disabled')),
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to        TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT dependency_identity_key
        UNIQUE (org_id, from_service_id, dependency_key, valid_from),
    CONSTRAINT dependency_valid_window
        CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS slos (
    slo_id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID        NOT NULL REFERENCES organizations (org_id),
    service_id     UUID        NOT NULL REFERENCES services (service_id),
    kind           STRING      NOT NULL
        CHECK (kind IN ('latency', 'error_rate', 'availability', 'saturation')),
    threshold      FLOAT8      NOT NULL,
    window_seconds INT8        NOT NULL CHECK (window_seconds > 0),
    current_value  FLOAT8      NOT NULL DEFAULT 0,
    burn_rate      FLOAT8      NOT NULL DEFAULT 0,
    status         STRING      NOT NULL DEFAULT 'healthy'
        CHECK (status IN ('healthy', 'breaching')),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT slo_service_kind_key UNIQUE (org_id, service_id, kind)
);

CREATE TABLE IF NOT EXISTS metric_samples (
    sample_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID        NOT NULL REFERENCES organizations (org_id),
    service_id  UUID        NOT NULL REFERENCES services (service_id),
    metric      STRING      NOT NULL,
    value       FLOAT8      NOT NULL,
    sampled_at  TIMESTAMPTZ NOT NULL,
    metadata    JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS metric_samples_by_service
    ON metric_samples (org_id, service_id, metric, sampled_at DESC)
    STORING (value);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID        NOT NULL REFERENCES organizations (org_id),
    service_id           UUID        NOT NULL REFERENCES services (service_id),
    title                STRING      NOT NULL,
    severity             STRING      NOT NULL
        CHECK (severity IN ('SEV1', 'SEV2', 'SEV3', 'SEV4')),
    status               STRING      NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'mitigating', 'resolved', 'closed')),
    family_id            STRING,
    variant_id           STRING,
    root_cause_service_id UUID REFERENCES services (service_id),
    runbook_id           UUID,
    session_id           UUID,
    opened_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at          TIMESTAMPTZ,
    closed_at            TIMESTAMPTZ,
    mttr_seconds         INT8,

    CONSTRAINT incident_time_order
        CHECK (resolved_at IS NULL OR resolved_at >= opened_at),
    CONSTRAINT incident_mttr_nonnegative
        CHECK (mttr_seconds IS NULL OR mttr_seconds >= 0)
);

CREATE INDEX IF NOT EXISTS incidents_feed
    ON incidents (org_id, status, opened_at DESC)
    STORING (service_id, severity, title, family_id, variant_id);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID        NOT NULL REFERENCES organizations (org_id),
    incident_id UUID        REFERENCES incidents (incident_id),
    service_id  UUID        NOT NULL REFERENCES services (service_id),
    slo_id      UUID        REFERENCES slos (slo_id),
    signal      STRING      NOT NULL,
    payload     JSONB       NOT NULL DEFAULT '{}',
    fired_at    TIMESTAMPTZ NOT NULL,
    cleared_at  TIMESTAMPTZ,

    CONSTRAINT alert_time_order CHECK (cleared_at IS NULL OR cleared_at >= fired_at)
);

CREATE INDEX IF NOT EXISTS alerts_open
    ON alerts (org_id, fired_at DESC)
    STORING (incident_id, service_id, signal)
    WHERE cleared_at IS NULL;

CREATE TABLE IF NOT EXISTS orders (
    order_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL REFERENCES organizations (org_id),
    user_id      UUID,
    status       STRING      NOT NULL
        CHECK (status IN ('pending', 'authorized', 'captured', 'succeeded', 'failed')),
    amount_cents INT8        NOT NULL CHECK (amount_cents >= 0),
    error_code   STRING,
    incident_id  UUID        REFERENCES incidents (incident_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS orders_by_incident
    ON orders (org_id, incident_id, created_at)
    STORING (status, amount_cents, error_code);

CREATE TABLE IF NOT EXISTS config_values (
    config_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID        NOT NULL REFERENCES organizations (org_id),
    service_id  UUID        NOT NULL REFERENCES services (service_id),
    key         STRING      NOT NULL,
    value       JSONB       NOT NULL,
    valid_from  TIMESTAMPTZ NOT NULL,
    valid_to    TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      STRING      NOT NULL,

    CONSTRAINT config_valid_window CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS config_current
    ON config_values (org_id, service_id, key)
    WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------------
-- Persistent memory
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS episodic_events (
    event_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL REFERENCES organizations (org_id),
    agent_id      UUID        NOT NULL,
    incident_id   UUID        REFERENCES incidents (incident_id),
    session_id    UUID,
    service_id    UUID        REFERENCES services (service_id),
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type    STRING      NOT NULL
        CHECK (event_type IN (
            'alert', 'observation', 'tool_call', 'decision',
            'action', 'outcome', 'human_message'
        )),
    content       STRING,
    raw_ref       STRING,
    metadata      JSONB       NOT NULL DEFAULT '{}',
    runbook_id    UUID,
    importance    FLOAT8      NOT NULL DEFAULT 0.5
        CHECK (importance >= 0 AND importance <= 1),
    embedding     VECTOR(1024),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, event_id),
    CONSTRAINT episodic_event_id_key UNIQUE (event_id)
);

CREATE TABLE IF NOT EXISTS semantic_facts (
    fact_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL REFERENCES organizations (org_id),
    agent_id      UUID        NOT NULL,
    subject       STRING      NOT NULL,
    predicate     STRING      NOT NULL,
    object        JSONB       NOT NULL,
    confidence    FLOAT8      NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0 AND confidence <= 1),
    source        STRING,
    embedding     VECTOR(1024),
    valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to      TIMESTAMPTZ,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by UUID,

    PRIMARY KEY (org_id, subject, predicate, fact_id),
    CONSTRAINT semantic_fact_id_key UNIQUE (fact_id),
    CONSTRAINT semantic_valid_window CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT semantic_superseded_fk
        FOREIGN KEY (superseded_by) REFERENCES semantic_facts (fact_id)
);

CREATE TABLE IF NOT EXISTS procedural_memory (
    runbook_id                    UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id                        UUID        NOT NULL REFERENCES organizations (org_id),
    agent_id                      UUID        NOT NULL,
    name                          STRING      NOT NULL,
    version                       INT8        NOT NULL DEFAULT 1 CHECK (version > 0),
    status                        STRING      NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'deprecated')),
    trigger_desc                  STRING      NOT NULL,
    embedding                     VECTOR(1024),
    applicable_service_tags       STRING[]    NOT NULL DEFAULT '{}',
    applicable_error_signatures   STRING[]    NOT NULL DEFAULT '{}',
    preconditions                 JSONB       NOT NULL DEFAULT '[]',
    steps                         JSONB       NOT NULL,
    postconditions                JSONB       NOT NULL DEFAULT '[]',
    usage_count                   INT8        NOT NULL DEFAULT 0 CHECK (usage_count >= 0),
    success_count                 INT8        NOT NULL DEFAULT 0 CHECK (success_count >= 0),
    failure_count                 INT8        NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    success_rate                  FLOAT8      NOT NULL DEFAULT 0
        CHECK (success_rate >= 0 AND success_rate <= 1),
    avg_resolution_seconds        INT8,
    last_used_at                  TIMESTAMPTZ,
    created_by                    STRING      NOT NULL DEFAULT 'consolidation_job',
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, agent_id, name, version),
    CONSTRAINT procedural_runbook_id_key UNIQUE (runbook_id),
    CONSTRAINT procedural_counts_consistent
        CHECK (success_count + failure_count <= usage_count)
);

ALTER TABLE incidents
    ADD CONSTRAINT incidents_runbook_fk
    FOREIGN KEY (runbook_id) REFERENCES procedural_memory (runbook_id);

ALTER TABLE episodic_events
    ADD CONSTRAINT episodic_runbook_fk
    FOREIGN KEY (runbook_id) REFERENCES procedural_memory (runbook_id);

CREATE TABLE IF NOT EXISTS runbook_provenance (
    runbook_id        UUID        NOT NULL REFERENCES procedural_memory (runbook_id),
    incident_id       UUID        NOT NULL REFERENCES incidents (incident_id),
    episodic_event_id UUID        REFERENCES episodic_events (event_id) ON DELETE SET NULL,
    role              STRING      NOT NULL
        CHECK (role IN ('source', 'reinforcement', 'counterexample')),
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (runbook_id, incident_id, recorded_at)
);

CREATE TABLE IF NOT EXISTS session_turns (
    session_id  UUID        NOT NULL,
    turn_index  INT8        NOT NULL,
    org_id      UUID        NOT NULL REFERENCES organizations (org_id),
    agent_id    UUID        NOT NULL,
    incident_id UUID        REFERENCES incidents (incident_id),
    role        STRING      NOT NULL CHECK (role IN ('sre', 'assistant', 'tool', 'system')),
    content     STRING,
    tool_calls  JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, turn_index)
) WITH (ttl_expire_after = '7 days');

CREATE TABLE IF NOT EXISTS session_state (
    session_id  UUID        NOT NULL PRIMARY KEY,
    org_id      UUID        NOT NULL REFERENCES organizations (org_id),
    agent_id    UUID        NOT NULL,
    incident_id UUID        REFERENCES incidents (incident_id),
    scratchpad  JSONB       NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (ttl_expire_after = '72 hours');

-- ---------------------------------------------------------------------------
-- Action provenance and event/evaluation instrumentation
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS remediation_actions (
    action_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID        NOT NULL REFERENCES organizations (org_id),
    incident_id     UUID        NOT NULL REFERENCES incidents (incident_id),
    action_type     STRING      NOT NULL
        CHECK (action_type IN (
            'rollback_deploy', 'scale_service', 'restart_service',
            'set_config', 'failover_dependency', 'throttle_traffic',
            'open_incident', 'escalate', 'resolve', 'no_op_page_human'
        )),
    target_id       UUID,
    params          JSONB       NOT NULL DEFAULT '{}',
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by      STRING      NOT NULL,
    outcome         STRING      NOT NULL
        CHECK (outcome IN ('success', 'failed', 'no_effect', 'pending')),
    memory_ref      UUID        REFERENCES episodic_events (event_id) ON DELETE SET NULL,
    transaction_id  UUID        NOT NULL DEFAULT gen_random_uuid(),
    idempotency_key STRING      NOT NULL,

    CONSTRAINT remediation_idempotency_key UNIQUE (org_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS remediation_by_incident
    ON remediation_actions (org_id, incident_id, applied_at)
    STORING (action_type, outcome, memory_ref, transaction_id);

CREATE TABLE IF NOT EXISTS agent_events (
    agent_event_id UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID        NOT NULL REFERENCES organizations (org_id),
    agent_id       UUID        NOT NULL,
    incident_id    UUID        REFERENCES incidents (incident_id),
    event_type     STRING      NOT NULL
        CHECK (event_type IN ('perceive', 'recall', 'reason', 'act', 'record', 'error')),
    payload        JSONB       NOT NULL,
    error_code     STRING,
    emitted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agent_events_timeline
    ON agent_events (org_id, incident_id, emitted_at)
    STORING (event_type, payload, error_code);

CREATE TABLE IF NOT EXISTS eval_probes (
    probe_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id         UUID        NOT NULL REFERENCES organizations (org_id),
    probe_type     STRING      NOT NULL
        CHECK (probe_type IN (
            'read_your_write', 'cross_agent_visibility',
            'atomicity', 'rpo', 'rto'
        )),
    status         STRING      NOT NULL CHECK (status IN ('pass', 'fail')),
    measured_value FLOAT8,
    unit           STRING,
    details        JSONB       NOT NULL DEFAULT '{}',
    measured_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
