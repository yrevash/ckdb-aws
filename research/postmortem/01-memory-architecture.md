# 01 — Memory Architecture

**Owner:** this doc. **Obeys:** `00-charter.md` (do not diverge without an explicit "⚠️ Charter
challenge"). **Builds on:** `../deep-dive/cockroachdb/04-memory-data-modeling.md` (starter schema),
`../deep-dive/cockroachdb/02-vector-search-and-cspann.md` (C-SPANN syntax/limits),
`../deep-dive/competitors/02-agent-memory-frameworks.md` (SOTA memory patterns).

This is the **contract**. `02` (agent + consolidation compute), `03` (AWS glue), `04` (cluster
topology/failover), `05` (dataset/eval), `06` (console UX) all consume the tables, columns, and query
patterns defined here. Nothing here contradicts the charter's wedge: memory and operational data are
**one store, one transaction**, read-your-own-writes has **zero lag**, and the whole thing survives a
region loss with **zero data loss**.

No application code below — DDL and illustrative SQL only, per the charter's planning-doc rule.

---

## 0. Scope columns (used consistently across every table)

Every memory row and every operational row that the agent touches carries a subset of these columns.
Fixing them once avoids five different "which column means what" conventions across tables.

| Column | Meaning | Notes |
|---|---|---|
| `org_id` | Tenant — the customer company running Postmortem against their SUM. | Leads every primary key. Also the `REGIONAL BY ROW` homing axis (§7) and the vector-index prefix column (§4). |
| `agent_id` | Which agent *instance* wrote/owns this row. | Single agent in MVP; supports the stretch multi-agent split (`detector`/`responder`/`consolidator`) without a schema change — it's just a value. |
| `service_id` | The SUM topology node (a microservice) this memory concerns. | FK into `services` (§3). Absent for org-wide facts. |
| `incident_id` | The SRE domain object — one on-call episode from alert to resolution. | Groups episodic events, working memory, and the runbook that was applied for timeline reconstruction and consolidation input. |
| `session_id` | The ChatOps console conversation (component U). | Usually 1:1 with `incident_id` in v1 but kept distinct — an SRE can reopen a console session against a closed incident, and a future multi-agent build may run several conversational sessions per incident. |
| `home_region` | Data-residency / locality-homing column (stretch). | Only present on tables using `REGIONAL BY ROW` (§7). Set explicitly from the org's registered residency at write time — never default to `gateway_region()` for this (see `04`-memory-data-modeling §5 nuance). |

`user_id` (the human SRE) is included only where relevant (working memory, provenance) — Postmortem is
agent-to-system more than agent-to-user, so it's not a load-bearing scope axis the way it is in a
consumer chat agent.

---

## 1. The four memory types

### 1.1 Episodic memory — the incident event log

Append-only. Every alert, observation, tool call, decision, action, and outcome the agent produces
during an incident lands here. High write volume, read via vector similarity (recall a *similar past
incident*) and via incident timeline (the console's memory panel).

```sql
CREATE TABLE episodic_events (
    event_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    incident_id   UUID,                                   -- NULL only for pre-incident chatter, if any
    session_id    UUID,
    service_id    UUID,                                   -- which SUM service this event concerns
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),      -- business time: when it happened
    event_type    STRING      NOT NULL,                    -- 'alert'|'observation'|'tool_call'|'decision'
                                                             -- |'action'|'outcome'|'human_message'
    content       STRING,                                  -- summarized/truncated text for LLM re-injection
    raw_ref       STRING,                                  -- s3:// pointer to full raw payload if content
                                                             -- was truncated (see §8 row-size guardrail)
    metadata      JSONB       NOT NULL DEFAULT '{}',       -- tool name/args, alert payload, latency_ms...
    runbook_id    UUID,                                    -- set on 'action' events: which runbook fired
    importance    FLOAT8      NOT NULL DEFAULT 0.5,        -- 0..1, drives TTL decay (§5) and consolidation
                                                             -- priority; bumped by the consolidation job
                                                             -- when an episode feeds a fact/runbook (§6)
    embedding     VECTOR(1024),                             -- unit-normalized (§4.2)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),       -- system time

    PRIMARY KEY (org_id, event_id),                         -- leads with org_id: spreads the append-only
                                                             -- log across ranges by tenant, not one hot
                                                             -- range at the "end" of a global timeline

    VECTOR INDEX (org_id, agent_id, embedding),             -- prefix-scoped ANN: one K-means tree per
                                                             -- (org, agent) pair
    INVERTED INDEX (metadata),

    INDEX episodic_by_incident (org_id, incident_id, occurred_at DESC)
      STORING (event_type, content, service_id, runbook_id) -- powers the console timeline panel (06)
);
```

### 1.2 Semantic memory — bitemporal facts about services/topology

Subject/predicate/object facts the agent has learned about the SUM: dependencies, ownership, known
issues, recurring failure signatures, SLO baselines. This is the table that needs bitemporal treatment
— see §2.

```sql
CREATE TABLE semantic_facts (
    fact_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    subject       STRING      NOT NULL,   -- 'service:checkout' | 'service:payments' | 'org:acme'
    predicate     STRING      NOT NULL,   -- 'depends_on' | 'known_issue' | 'owner_team' |
                                          -- 'p50_recovery_seconds' | 'safe_rollback_target'
    object        JSONB       NOT NULL,   -- typed value, keeps schema flexible
    confidence    FLOAT8      NOT NULL DEFAULT 1.0,
    source        STRING,                 -- 'consolidation_job' | 'human_stated' | 'tool:topology_scan'
    embedding     VECTOR(1024),           -- embedding of "subject predicate: object" for fuzzy fact recall

    valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- business time: when true in the world
    valid_to      TIMESTAMPTZ,                          -- NULL = currently believed true
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),   -- system time: when CockroachDB learned it
    superseded_by UUID REFERENCES semantic_facts (fact_id),

    PRIMARY KEY (org_id, subject, predicate, fact_id),
    VECTOR INDEX (org_id, agent_id, embedding),

    INDEX semantic_current (org_id, subject, predicate)
      STORING (object, confidence) WHERE valid_to IS NULL   -- partial index: "what do we believe now"
);
```

### 1.3 Procedural memory — the frontier: matchable, executable runbooks

This is the table the charter calls "the frontier," so it gets the most design attention. A runbook
must do two jobs a single JSONB blob can't do carelessly:

1. **Be matchable** — an agent facing a *new* incident needs to find the *right* past runbook by
   semantic similarity of the situation, then narrow by structured applicability, then rank by track
   record. That's a three-stage retrieval, not a single vector query (§4.1).
2. **Be executable** — once matched, the agent needs a machine-actionable step sequence it can run
   against its own tool interface (owned by `02`), with built-in safety gates, because the charter's
   wedge requires the agent to **act on real operational data**, not just suggest text.

```sql
CREATE TABLE procedural_memory (
    runbook_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    name          STRING      NOT NULL,                    -- stable identity across versions,
                                                             -- e.g. 'rollback-on-5xx-spike'
    version       INT         NOT NULL DEFAULT 1,
    status        STRING      NOT NULL DEFAULT 'draft',     -- 'draft'|'active'|'deprecated'
                                                             -- draft = consolidation-distilled, unproven;
                                                             -- active = promoted after N successful uses
                                                             -- or human approval; deprecated = superseded

    -- ---- matching surface ----
    trigger_desc          STRING  NOT NULL,                 -- NL "when to use this" — embedded below
    embedding              VECTOR(1024),                    -- embedding of trigger_desc
    applicable_service_tags STRING[] NOT NULL DEFAULT '{}', -- e.g. {'checkout','payments'}; empty = generic
    applicable_error_signatures STRING[] NOT NULL DEFAULT '{}', -- normalized alert/error fingerprints
    preconditions JSONB      NOT NULL DEFAULT '[]',         -- structured checks the agent verifies before
                                                             -- running, e.g. [{"metric":"error_rate_5xx",
                                                             -- "op":">","threshold":0.05,"window_s":300}]

    -- ---- executable surface ----
    steps         JSONB       NOT NULL,                     -- ordered step list, see shape below
    postconditions JSONB      NOT NULL DEFAULT '[]',        -- verification checks that confirm success

    -- ---- track record (feedback loop, written by the consolidation job — §6) ----
    usage_count      INT8    NOT NULL DEFAULT 0,
    success_count     INT8    NOT NULL DEFAULT 0,
    failure_count     INT8    NOT NULL DEFAULT 0,
    success_rate       FLOAT8 NOT NULL DEFAULT 0,           -- success_count / GREATEST(usage_count,1),
                                                             -- denormalized for cheap ORDER BY at read time
    avg_resolution_seconds INT8,
    last_used_at      TIMESTAMPTZ,

    -- ---- provenance / audit (stretch: signed provenance trail) ----
    created_by     STRING     NOT NULL DEFAULT 'consolidation_job',  -- or 'human:<sre_id>'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, agent_id, name, version),
    VECTOR INDEX (org_id, status, embedding),               -- see §4.1 — status as a prefix column,
                                                             -- not a partial-index predicate: C-SPANN
                                                             -- prefix filtering requires equality/IN on
                                                             -- literal index columns, not a WHERE clause
    INVERTED INDEX (preconditions),
    INDEX procedural_active (org_id, name) STORING (runbook_id, version)
      WHERE status = 'active'                               -- "give me the live version of runbook X"
);

-- Provenance join table: which incidents/episodes gave rise to (or reinforced) a runbook.
-- Kept separate from procedural_memory rather than as an array column so a many-incidents-to-one-runbook
-- history stays queryable and doesn't bloat the hot row read on every match query.
CREATE TABLE runbook_provenance (
    runbook_id       UUID NOT NULL,
    incident_id      UUID NOT NULL,
    episodic_event_id UUID,                                 -- the specific 'action'/'outcome' event, if any
    role             STRING NOT NULL,                        -- 'source'|'reinforcement'|'counterexample'
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (runbook_id, incident_id, recorded_at)
);
```

**`steps` JSONB shape** (illustrative — the tool-name enum and arg schema are `02`'s contract; this is
the storage envelope `01` owns):

```json
[
  {
    "step": 1,
    "tool": "rollback_deploy",
    "args": { "service_id": "{{incident.service_id}}", "target_version": "{{last_known_good_version}}" },
    "reversible": true,
    "requires_human_approval": false,
    "max_duration_seconds": 120,
    "verify": { "type": "metric_check", "metric": "error_rate_5xx", "op": "<", "threshold": 0.01, "within_seconds": 300 },
    "on_failure": "escalate_to_human"
  },
  {
    "step": 2,
    "tool": "notify_channel",
    "args": { "message": "Rolled back {{incident.service_id}} to {{last_known_good_version}}" },
    "reversible": true,
    "requires_human_approval": false
  }
]
```

The safety gates (`reversible`, `requires_human_approval`, `on_failure`) are the schema-level hook the
agent's action loop (`02`) reads to decide whether a step can run autonomously or needs a human-in-the-
loop confirmation in the console — this is what keeps "the agent acts on real operational data" from
being reckless.

**Why versioned identity `(org_id, agent_id, name, version)` instead of mutating `steps` in place:**
an audit trail of how a runbook evolved is what lets the console show "this runbook changed after
incident #482 because the rollback step alone wasn't sufficient" — cheap insurance for both debugging
and the "explainable memory" story.

### 1.4 Working memory — incident session state

Short-lived, high-churn. Two shapes, same as the generic pattern in `04`-memory-data-modeling, but
TTL'd against **incident duration**, not chat-session duration — incidents can run for hours, so the
4-hour default used in generic agent memory is too aggressive here.

```sql
CREATE TABLE session_turns (
    session_id    UUID        NOT NULL,
    turn_index    INT8        NOT NULL,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    incident_id   UUID,
    role          STRING      NOT NULL,        -- 'sre'|'assistant'|'tool'|'system'
    content       STRING,
    tool_calls    JSONB       NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, turn_index)
) WITH (ttl_expire_after = '7 days');   -- outlives the incident for post-incident review/replay in the
                                        -- console, but is gone well before it matters for storage cost

CREATE TABLE session_state (
    session_id    UUID        NOT NULL PRIMARY KEY,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    incident_id   UUID,
    scratchpad    JSONB       NOT NULL DEFAULT '{}',  -- current hypothesis, candidate runbooks retrieved,
                                                        -- steps executed so far, pending verifications
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (ttl_expire_after = '72 hours');  -- generous relative to typical incident duration; no vector
                                        -- index — looked up by session_id, not similarity, keep it cheap

-- Idempotent scratchpad upsert — safe to call every agent turn.
UPSERT INTO session_state (session_id, org_id, agent_id, incident_id, scratchpad, updated_at)
VALUES ($1, $2, $3, $4, $5, now());
```

---

## 2. Bitemporal facts: transition, don't overwrite

Two independent time axes, per `04`-memory-data-modeling §2:

- **Valid time** (`valid_from`/`valid_to`) — when the fact was true *in the SUM's world* (e.g., "service
  checkout depended on the legacy auth service *until* the auth migration completed on date D").
- **System time** (`recorded_at`) — when Postmortem itself learned the fact. Always monotonic.

This is deliberately **not** CockroachDB's own MVCC `AS OF SYSTEM TIME` — that only reaches back through
the GC window (~25h default) and answers "what did the row look like," not "what did the agent believe
and why does it no longer believe it." Bitemporal columns give unbounded, queryable belief history,
which is exactly what an SRE asking "why did you think a rollback would fix this?" needs.

**Atomic transition — one implicit (server-auto-retried) statement:**

```sql
WITH close_old AS (
    UPDATE semantic_facts
    SET valid_to = now()
    WHERE org_id = $1 AND subject = $2 AND predicate = $3 AND valid_to IS NULL
    RETURNING fact_id
),
new_fact AS (
    INSERT INTO semantic_facts (org_id, agent_id, subject, predicate, object, source, embedding)
    SELECT $1, $4, $2, $3, $5, $6, $7
    RETURNING fact_id
)
UPDATE semantic_facts
SET superseded_by = (SELECT fact_id FROM new_fact)
WHERE fact_id = (SELECT fact_id FROM close_old);
```

Example: an incident reveals `service:checkout` now depends on a new `service:fraud-scoring` call added
in last week's deploy — the consolidation job closes the old `depends_on` fact and opens a new one in
one round trip, no client retry loop needed.

**Current belief:** `WHERE valid_to IS NULL` (served by `semantic_current`).

**Point-in-time belief** ("what did we believe about checkout's dependencies on the day of incident
#482"):

```sql
SELECT object, confidence, source
FROM semantic_facts
WHERE org_id = $1 AND subject = $2 AND predicate = $3
  AND valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
ORDER BY recorded_at DESC
LIMIT 1;
```

**Belief history** (powers the console's "why did the agent think X" explainability view — component U):

```sql
SELECT object, confidence, valid_from, valid_to, recorded_at, source
FROM semantic_facts
WHERE org_id = $1 AND subject = $2 AND predicate = $3
ORDER BY recorded_at;
```

---

## 3. The co-location contract

This is the wedge. The charter is explicit: operational-data tables (services/deploys/incidents/orders)
and memory tables must live in CockroachDB together so a memory write and an operational action commit
in **one ACID transaction** — never two systems, never a dual-write.

**Note on ownership:** the tables below are an **illustrative minimal slice** sufficient to demonstrate
the co-location pattern and give `02`/`03`/`06` something concrete to build against. The canonical,
full operational schema (all SUM tables, seed data, simulator behavior) is owned by `05`
(data-and-evaluation). `05` should extend — not contradict — the shapes below; if `05` needs different
columns, that's a cross-doc note, not a silent fork.

```sql
CREATE TABLE services (
    service_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID        NOT NULL,
    name              STRING      NOT NULL,
    tier              STRING      NOT NULL DEFAULT 'standard',  -- 'critical-path'|'standard'
    owner_team        STRING,
    current_deploy_id UUID,
    status            STRING      NOT NULL DEFAULT 'healthy',   -- 'healthy'|'degraded'|'down'
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE deploys (
    deploy_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL,
    service_id   UUID        NOT NULL REFERENCES services (service_id),
    version      STRING      NOT NULL,
    action       STRING      NOT NULL DEFAULT 'deploy',         -- 'deploy'|'rollback'|'scale'|'restart'
    deployed_by  STRING      NOT NULL,                          -- 'agent:postmortem' | 'human:<sre_id>'
    status       STRING      NOT NULL DEFAULT 'in_progress',
    deployed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE incidents (
    incident_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL,
    service_id   UUID        NOT NULL REFERENCES services (service_id),
    severity     STRING      NOT NULL,
    status       STRING      NOT NULL DEFAULT 'open',           -- 'open'|'mitigating'|'closed'
    runbook_id   UUID,                                          -- which procedural memory was applied
    session_id   UUID,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at    TIMESTAMPTZ
);

CREATE TABLE orders (                                           -- the checkout critical path
    order_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL,
    status       STRING      NOT NULL,                          -- 'succeeded'|'failed'|'pending'
    amount_cents INT8,
    error_code   STRING,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Example transaction: recall informed an action, one commit

The agent recalled runbook `$runbook_id` (matched via §4.1), executes its first step (a rollback), and
records the episodic memory of doing so — all as one implicit CTE transaction. If any part fails, none
of it commits: the operational rollback and the memory of having done it can never disagree.

```sql
WITH action AS (
    INSERT INTO deploys (org_id, service_id, version, action, deployed_by, status)
    VALUES ($org_id, $service_id, $prior_good_version, 'rollback', 'agent:postmortem', 'completed')
    RETURNING deploy_id
),
svc AS (
    UPDATE services
    SET status = 'recovering', current_deploy_id = (SELECT deploy_id FROM action), updated_at = now()
    WHERE service_id = $service_id
    RETURNING service_id
),
inc AS (
    UPDATE incidents SET status = 'mitigating', runbook_id = $runbook_id
    WHERE incident_id = $incident_id
    RETURNING incident_id
),
episode AS (
    INSERT INTO episodic_events (org_id, agent_id, incident_id, session_id, service_id, event_type,
                                   content, metadata, runbook_id, importance, embedding)
    SELECT $org_id, $agent_id, $incident_id, $session_id, $service_id, 'action',
           'Rolled back ' || $service_id || ' to ' || $prior_good_version || ' per runbook ' || $runbook_id,
           jsonb_build_object('deploy_id', (SELECT deploy_id FROM action)),
           $runbook_id, 0.9, $embedding
    RETURNING event_id
),
rb AS (
    UPDATE procedural_memory SET usage_count = usage_count + 1, last_used_at = now()
    WHERE runbook_id = $runbook_id
    RETURNING runbook_id
)
SELECT (SELECT deploy_id FROM action) AS deploy_id, (SELECT event_id FROM episode) AS event_id;
```

This is a single statement — an **implicit transaction**, auto-retried server-side on `40001`, one round
trip from the agent's process, and it is the literal proof point for demo-thesis beat #2 ("memory + action
are one transaction — show it").

---

## 4. Retrieval design

### 4.1 Three-stage recall: ANN → structured filter → rerank

A single `ORDER BY embedding <-> $1 LIMIT k` is not enough for procedural memory — "semantically similar
incident" and "runbook I'm actually allowed/able to run right now" are different questions. The pattern:

**Stage 1 — prefix-scoped ANN.** C-SPANN accelerates filtering only on **equality/IN prefix columns**
of the vector index (`02`-vector-search-and-cspann §3.5). `procedural_memory`'s index is
`(org_id, status, embedding)` — both `org_id` and `status = 'active'` are equality predicates, so the
search is genuinely scoped inside the index walk, not post-filtered:

```sql
SELECT runbook_id, name, version, trigger_desc, applicable_service_tags,
       applicable_error_signatures, preconditions, steps, success_rate, usage_count, last_used_at
FROM procedural_memory
WHERE org_id = $1 AND status = 'active'
ORDER BY embedding <=> $2::VECTOR(1024)   -- cosine distance, unit-normalized embeddings (§4.2)
LIMIT 20;                                  -- over-fetch N=20 for stage 2/3 to rerank against
```

**Stage 2 — structured filter (application-side or a follow-up `WHERE`).** Drop candidates whose
`applicable_service_tags` don't overlap the incident's service, or whose `preconditions` aren't
currently satisfied (the agent checks live metrics against `preconditions` before trusting a candidate).
This is exactly the "filtering improves accuracy for free" guidance from `02`-vector-search-and-cspann
§4.3 — narrowing here is cheaper and more precise than cranking beam size globally.

**Stage 3 — rerank by track record.** Score the surviving candidates:

```
score = w1 * (1 - cosine_distance) + w2 * success_rate + w3 * recency_decay(last_used_at)
```

Return the top 3 to the agent's reasoning step, with `steps`/`preconditions` attached so it can both
explain its choice ("closest match, 82% historical success rate, used 4 times") and execute directly.

Episodic and semantic recall are simpler — single-stage prefix-scoped ANN plus an optional structured
`WHERE` on already-equality-constrained scope columns:

```sql
-- "Have we seen something like this before?"
SELECT event_id, occurred_at, content, metadata, runbook_id
FROM episodic_events
WHERE org_id = $1 AND agent_id = $2
ORDER BY embedding <=> $3::VECTOR(1024)
LIMIT 8;
```

### 4.2 Embedding strategy

- **Unit-normalize every embedding before storage**, regardless of which distance opclass is used —
  cheap defense-in-depth, and required for the L2/cosine-equivalence fallback if `02`/`03` ever need to
  degrade to `vector_l2_ops` on a cluster version where cosine isn't yet index-accelerated.
- **Distance opclass: `vector_cosine_ops` (`<=>`)** on every vector index in this schema. Cosine
  distance is the right metric for semantic-similarity-of-text (incident descriptions, runbook
  triggers, facts), and it's index-accelerated from **v25.3 onward** (`02`-vector-search-and-cspann
  §1). This schema **assumes a v25.3+/v26.x target cluster** — `04` (cockroachdb-deployment-resilience)
  owns confirming the pinned version; if the provisioned cluster is pinned below v25.3, fall back to
  the default `vector_l2_ops` (L2 ranking over unit-normalized vectors is monotonically equivalent to
  cosine ranking, per `04`-memory-data-modeling §7 caveat table) and flag the gap.
- **Model/dimension: `VECTOR(1024)`, assuming Amazon Bedrock Titan Text Embeddings V2** (1024-dim
  default output, native `normalize=true` option) — keeps the whole stack on AWS, matches the charter's
  Bedrock baseline, and avoids a second embeddings vendor. **This is a decision `01` is making as
  schema owner but `02`/`03` must confirm** (they own the actual Bedrock model call) — if they pick a
  different embedding model/dimension, every `VECTOR(n)` column and index in this file must change
  before any data is ingested. `VECTOR(n)`'s dimension is enforced and effectively fixed once rows
  exist; changing it later means dropping and rebuilding the column and its index, not an in-place
  migration. **Lock this decision in week 1.**

### 4.3 Guaranteeing read-your-own-writes

The charter's #1 success metric is **0ms read-your-write staleness**. The mechanism:

- **Default (leaseholder) reads for every hot-path recall** — the recall queries above run with
  CockroachDB's default consistency, which observes all previously committed writes from any node in
  the cluster. This is inherent to CockroachDB's consistency model, not a special mode to opt into.
- **Never use `AS OF SYSTEM TIME follower_read_timestamp()` on the recall-before-decide loop.** Follower
  reads (`04`-memory-data-modeling §6) trade a few seconds of staleness for lower latency/load — perfect
  for the console's background timeline polling or dashboards (component U/06), **wrong** for the
  moment the agent is about to act on what it just wrote (e.g., re-reading `session_state.scratchpad`
  right after upserting it, or re-reading `episodic_events` right after recording an action to decide
  the next step). Document this split explicitly for `02`: **write path and immediate-recall path use
  default reads; only UI-polling and analytics paths may use follower reads.**
- Because memory and operational writes commit in the same transaction (§3), there is no second system
  with its own replication lag to introduce staleness — this is the structural reason CockroachDB gets
  this metric essentially for free where a dual-write architecture (Postgres + Pinecone) cannot.

### 4.4 recall@k tuning

- C-SPANN's design target is **99%+ recall@k**; the charter's bar is **≥95%**, so there's headroom.
- Primary query-time knob: `SET vector_search_beam_size = 32` (default). Raise it if recall measured
  against `05`'s eval dataset falls short; lower it if p99 latency is the binding constraint.
- Build-time knobs (`min_partition_size`/`max_partition_size` at `CREATE VECTOR INDEX ... WITH (...)`)
  trade write throughput against read accuracy — leave at defaults (16/128) until `05`'s incident
  dataset is large enough to profile against.
- Prefix-scoped queries (every query in this schema is, by construction — §4.1) improve achievable
  recall at a given beam size for free, per `02`-vector-search-and-cspann §4.3 guidance. No universal
  numbers are published by Cockroach Labs; **empirical validation against the seeded incident corpus is
  `05`'s job** — this doc provides the knobs, `05` provides the ground truth to tune against.

---

## 5. Memory decay: importance-weighted TTL

Row-level TTL (`04`-memory-data-modeling §4) replaces a cron-job `DELETE` with a declarative background
sweep.

```sql
ALTER TABLE episodic_events SET (
    ttl_expiration_expression =
      "CASE WHEN importance >= 0.8 THEN occurred_at + INTERVAL '365 days'
            WHEN importance >= 0.4 THEN occurred_at + INTERVAL '90 days'
            ELSE occurred_at + INTERVAL '30 days' END",
    ttl_job_cron = '@daily'
);
```

**A known TTL constraint shapes this design:** `ttl_expiration_expression` is evaluated per-row from
that row's own columns — it cannot join to `incidents` to check "is this incident still open." That
means a low-importance event belonging to a *still-open* incident could, in principle, sit inside a
30-day floor with room to spare (incidents don't run 30 days), so this is a non-issue at Postmortem's
timescale — but the *intended* mechanism for "don't forget things that matter" is the consolidation
job (§6): when it distills an episode into a fact or runbook, it bumps that episode's `importance`
(e.g., `UPDATE episodic_events SET importance = GREATEST(importance, 0.8) WHERE event_id = ANY($ids)`),
which both raises its retention band *and* creates the linkage `runbook_provenance` records explicitly.
Importance isn't just a decay knob — it's the consolidation job's signal of "this episode mattered."

**Other tables' decay policy:**

| Table | Policy | Rationale |
|---|---|---|
| `semantic_facts` | No TTL on current facts; optionally sweep very old superseded ones (`valid_to IS NOT NULL AND recorded_at < now() - INTERVAL '2 years'`) | Bitemporal history has audit/explainability value — don't decay belief history the way you decay raw chatter. |
| `procedural_memory` | No TTL on `status = 'active'`; long-grace TTL on `deprecated` versions (e.g., 1 year) | Active runbooks are load-bearing; deprecated versions exist for audit, not indefinitely. |
| `session_turns` | Fixed `7 days` | Outlives typical incident + post-incident review window. |
| `session_state` | Fixed `72 hours` | Working memory only; incidents rarely run this long, and consolidation has already run by then. |

TTL deletes flow through the changefeed by default (they're ordinary deletes); leave this on for
`episodic_events` so the consolidation job's derived state can react to forgetting, and disable it
(`ttl_disable_changefeed_replication = 'true'`) on `session_turns`/`session_state` since working-memory
churn is pure noise for a consolidation consumer.

---

## 6. The consolidation job's schema-side contract

Compute (what the job reasons about, prompts, thresholds for draft→active promotion) is `02`/`03`'s
job. This section defines only the **tables it reads, the tables it writes, and the transactional shape
of those writes** — the interface `02`/`03` build against.

**Trigger:** a changefeed on `episodic_events` and `incidents` (specifically, incident `status` flipping
to `'closed'`), per `04`-memory-data-modeling §3 sink patterns (webhook → Lambda is the natural fit for
"consolidate shortly after an incident closes"; an S3 sink + daily batch is the fallback for pure
overnight consolidation). Wiring is `03`'s job.

**Reads:**
```sql
-- All episodes for a just-closed incident, in order
SELECT event_id, event_type, content, metadata, runbook_id, importance, embedding
FROM episodic_events
WHERE org_id = $1 AND incident_id = $2
ORDER BY occurred_at;

-- What we currently believe about the affected service (to decide UPDATE vs INSERT)
SELECT fact_id, predicate, object, confidence FROM semantic_facts
WHERE org_id = $1 AND subject = $2 AND valid_to IS NULL;

-- Existing runbooks that might already cover this pattern (to decide reinforce vs new)
SELECT runbook_id, name, version, success_rate FROM procedural_memory
WHERE org_id = $1 AND status = 'active'
ORDER BY embedding <=> $2::VECTOR(1024) LIMIT 5;
```

**Writes:**
- **Semantic facts** — the bitemporal transition CTE (§2), one call per fact that changed.
- **Procedural memory** — either reinforce an existing runbook's track record, or promote a new
  version. Both are single-statement CTEs, matching the transition pattern:

```sql
-- Reinforce: this incident's outcome matches an existing runbook
UPDATE procedural_memory
SET usage_count = usage_count + 1,
    success_count = success_count + CASE WHEN $outcome = 'success' THEN 1 ELSE 0 END,
    failure_count = failure_count + CASE WHEN $outcome = 'success' THEN 0 ELSE 1 END,
    success_rate = (success_count + CASE WHEN $outcome = 'success' THEN 1 ELSE 0 END)::FLOAT8
                   / GREATEST(usage_count + 1, 1),
    last_used_at = now()
WHERE runbook_id = $runbook_id;

-- Promote: distill a new/changed procedure, deprecate the prior active version, record provenance
WITH new_version AS (
    INSERT INTO procedural_memory (org_id, agent_id, name, version, status, trigger_desc, embedding,
                                    applicable_service_tags, applicable_error_signatures, preconditions,
                                    steps, created_by)
    SELECT $1, $2, $3, COALESCE(MAX(version), 0) + 1, 'draft', $4, $5, $6, $7, $8, $9, 'consolidation_job'
    FROM procedural_memory WHERE org_id = $1 AND agent_id = $2 AND name = $3
    RETURNING runbook_id
),
deprecate_old AS (
    UPDATE procedural_memory SET status = 'deprecated'
    WHERE org_id = $1 AND agent_id = $2 AND name = $3 AND status = 'active'
      AND runbook_id <> (SELECT runbook_id FROM new_version)
    RETURNING runbook_id
)
INSERT INTO runbook_provenance (runbook_id, incident_id, episodic_event_id, role)
SELECT (SELECT runbook_id FROM new_version), $incident_id, unnest($source_event_ids::UUID[]), 'source';
```

- **`draft` → `active` promotion** is a policy decision (`02` owns the threshold, e.g. "3 successful
  reinforcements") but is mechanically the same `UPDATE procedural_memory SET status = 'active' WHERE
  runbook_id = $1` — a one-line write against this schema.
- **Episode importance bump** (§5), so consolidated episodes decay slower.

None of the above needs client-side retry logic beyond the standard `40001` backoff `02`/`03` already
implement for explicit multi-step work — every write shown here is a single CTE statement, so it's an
implicit, server-auto-retried transaction.

---

## 7. Indexing, partitioning, and multi-tenant homing

### 7.1 Indexes already defined (recap)

| Table | Index | Purpose |
|---|---|---|
| `episodic_events` | `VECTOR INDEX (org_id, agent_id, embedding)` | Scoped ANN recall |
| `episodic_events` | `episodic_by_incident (org_id, incident_id, occurred_at DESC)` | Console timeline |
| `episodic_events` | `INVERTED INDEX (metadata)` | Ad-hoc JSONB filters |
| `semantic_facts` | `VECTOR INDEX (org_id, agent_id, embedding)` | Fuzzy fact recall |
| `semantic_facts` | `semantic_current (org_id, subject, predicate) WHERE valid_to IS NULL` | Current-belief lookups |
| `procedural_memory` | `VECTOR INDEX (org_id, status, embedding)` | Scoped runbook matching |
| `procedural_memory` | `procedural_active (org_id, name) WHERE status = 'active'` | Live-version lookup |
| `procedural_memory` | `INVERTED INDEX (preconditions)` | Structured applicability filters |

### 7.2 Partitioning: prefix columns *are* the partitioning strategy

Because C-SPANN builds effectively one K-means tree per distinct prefix-key value
(`02`-vector-search-and-cspann §2.5), leading every vector index with `org_id` already gives per-tenant
index isolation without a separate `PARTITION BY` clause — a large customer's incident volume can't
degrade another customer's recall quality or write throughput inside the shared index. No additional
manual range partitioning is needed for the MVP's scale.

### 7.3 REGIONAL BY ROW multi-tenant homing (stretch)

Schema-side homing only — the database-level region list and survival goal
(`ALTER DATABASE ... ADD REGION`, `SURVIVE REGION FAILURE`) are **owned by `04`
(cockroachdb-deployment-resilience)**; this section is inert until `04` configures those. What `01`
owns is making every memory table homeable:

```sql
ALTER TABLE episodic_events ADD COLUMN home_region crdb_internal_region NOT NULL DEFAULT
  gateway_region()::crdb_internal_region;   -- placeholder default for non-regulated dev; production
                                            -- writes must set home_region explicitly from the org's
                                            -- registered residency, never rely on gateway_region()
                                            -- (see 04-memory-data-modeling §5 nuance)

ALTER TABLE episodic_events ALTER PRIMARY KEY USING COLUMNS (home_region, org_id, event_id);
ALTER TABLE episodic_events SET LOCALITY REGIONAL BY ROW AS home_region;

-- Same pattern for semantic_facts, procedural_memory, and the operational tables in §3 if per-tenant
-- residency needs to extend to the SUM's own data, not just Postmortem's memory of it.
```

Applying this changes primary keys, which is a schema migration best done **before** data volume grows
— if the stretch goal is likely to land, do this homing pass in week 1–2, not as a retrofit in week 4.

---

## A. Consolidated starter schema (full DDL)

```sql
-- ============================================================
-- Postmortem — Memory + Operational Schema (v1)
-- One database, one transaction boundary, per charter §4.
-- Target cluster: v25.3+ / v26.x (cosine opclass index-accelerated — confirm with 04).
-- ============================================================

SET CLUSTER SETTING feature.vector_index.enabled = true;   -- required even on "GA" vector indexes (04 owns provisioning)

CREATE DATABASE IF NOT EXISTS postmortem;
USE postmortem;

-- ---------- Operational tables (illustrative; canonical/full version owned by 05) ----------
CREATE TABLE services (
    service_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID        NOT NULL,
    name              STRING      NOT NULL,
    tier              STRING      NOT NULL DEFAULT 'standard',
    owner_team        STRING,
    current_deploy_id UUID,
    status            STRING      NOT NULL DEFAULT 'healthy',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE deploys (
    deploy_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL,
    service_id   UUID        NOT NULL REFERENCES services (service_id),
    version      STRING      NOT NULL,
    action       STRING      NOT NULL DEFAULT 'deploy',
    deployed_by  STRING      NOT NULL,
    status       STRING      NOT NULL DEFAULT 'in_progress',
    deployed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE incidents (
    incident_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL,
    service_id   UUID        NOT NULL REFERENCES services (service_id),
    severity     STRING      NOT NULL,
    status       STRING      NOT NULL DEFAULT 'open',
    runbook_id   UUID,
    session_id   UUID,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at    TIMESTAMPTZ
);

CREATE TABLE orders (
    order_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID        NOT NULL,
    status       STRING      NOT NULL,
    amount_cents INT8,
    error_code   STRING,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Episodic memory ----------
CREATE TABLE episodic_events (
    event_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    incident_id   UUID,
    session_id    UUID,
    service_id    UUID,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type    STRING      NOT NULL,
    content       STRING,
    raw_ref       STRING,
    metadata      JSONB       NOT NULL DEFAULT '{}',
    runbook_id    UUID,
    importance    FLOAT8      NOT NULL DEFAULT 0.5,
    embedding     VECTOR(1024),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, event_id),
    VECTOR INDEX (org_id, agent_id, embedding vector_cosine_ops),
    INVERTED INDEX (metadata),
    INDEX episodic_by_incident (org_id, incident_id, occurred_at DESC)
      STORING (event_type, content, service_id, runbook_id)
) WITH (
    ttl_expiration_expression =
      "CASE WHEN importance >= 0.8 THEN occurred_at + INTERVAL '365 days'
            WHEN importance >= 0.4 THEN occurred_at + INTERVAL '90 days'
            ELSE occurred_at + INTERVAL '30 days' END",
    ttl_job_cron = '@daily'
  );

-- ---------- Semantic memory (bitemporal) ----------
CREATE TABLE semantic_facts (
    fact_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    subject       STRING      NOT NULL,
    predicate     STRING      NOT NULL,
    object        JSONB       NOT NULL,
    confidence    FLOAT8      NOT NULL DEFAULT 1.0,
    source        STRING,
    embedding     VECTOR(1024),

    valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to      TIMESTAMPTZ,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by UUID REFERENCES semantic_facts (fact_id),

    PRIMARY KEY (org_id, subject, predicate, fact_id),
    VECTOR INDEX (org_id, agent_id, embedding vector_cosine_ops),
    INDEX semantic_current (org_id, subject, predicate)
      STORING (object, confidence) WHERE valid_to IS NULL
);

-- ---------- Procedural memory (learned runbooks) ----------
CREATE TABLE procedural_memory (
    runbook_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    name          STRING      NOT NULL,
    version       INT         NOT NULL DEFAULT 1,
    status        STRING      NOT NULL DEFAULT 'draft',

    trigger_desc  STRING      NOT NULL,
    embedding     VECTOR(1024),
    applicable_service_tags STRING[] NOT NULL DEFAULT '{}',
    applicable_error_signatures STRING[] NOT NULL DEFAULT '{}',
    preconditions JSONB       NOT NULL DEFAULT '[]',

    steps         JSONB       NOT NULL,
    postconditions JSONB      NOT NULL DEFAULT '[]',

    usage_count       INT8    NOT NULL DEFAULT 0,
    success_count      INT8   NOT NULL DEFAULT 0,
    failure_count      INT8   NOT NULL DEFAULT 0,
    success_rate        FLOAT8 NOT NULL DEFAULT 0,
    avg_resolution_seconds INT8,
    last_used_at       TIMESTAMPTZ,

    created_by    STRING      NOT NULL DEFAULT 'consolidation_job',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, agent_id, name, version),
    VECTOR INDEX (org_id, status, embedding vector_cosine_ops),
    INVERTED INDEX (preconditions),
    INDEX procedural_active (org_id, name) STORING (runbook_id, version)
      WHERE status = 'active'
);

CREATE TABLE runbook_provenance (
    runbook_id        UUID NOT NULL,
    incident_id       UUID NOT NULL,
    episodic_event_id UUID,
    role              STRING NOT NULL,
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (runbook_id, incident_id, recorded_at)
);

-- ---------- Working memory (short TTL) ----------
CREATE TABLE session_turns (
    session_id    UUID        NOT NULL,
    turn_index    INT8        NOT NULL,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    incident_id   UUID,
    role          STRING      NOT NULL,
    content       STRING,
    tool_calls    JSONB       NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, turn_index)
) WITH (ttl_expire_after = '7 days');

CREATE TABLE session_state (
    session_id    UUID        NOT NULL PRIMARY KEY,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    incident_id   UUID,
    scratchpad    JSONB       NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (ttl_expire_after = '72 hours');

-- ---------- CDC: trigger consolidation (wiring owned by 03) ----------
CREATE EXTERNAL CONNECTION postmortem_consolidation_webhook
  AS 'webhook-https://<api-id>.execute-api.<region>.amazonaws.com/prod/consolidate';

CREATE CHANGEFEED FOR TABLE episodic_events, incidents
INTO 'external://postmortem_consolidation_webhook'
WITH updated,
     resolved = '10s',
     min_checkpoint_frequency = '5s',
     webhook_sink_config = '{"Flush":{"Bytes":1048576,"Messages":100,"Frequency":"5s"}}';
```

---

## B. Decisions & recommendations

| Decision | Choice | Rationale |
|---|---|---|
| Embedding model / dims | Amazon Bedrock Titan Text Embeddings V2, `VECTOR(1024)`, native `normalize=true` | Stays AWS-native per charter baseline, avoids a second embeddings vendor, unit-normalized output out of the box. **Must be confirmed by `02`/`03` before ingestion starts** — dimension changes require rebuilding every `VECTOR` column/index. |
| Distance metric / opclass | `vector_cosine_ops` (`<=>`) on every vector index | Cosine is the right metric for text-similarity recall; index-accelerated from v25.3 (verify against `04`'s pinned cluster version). Fallback: default `vector_l2_ops`, which is rank-equivalent for unit-normalized vectors. |
| PK strategy for the episodic log | Lead with `org_id`, `gen_random_uuid()` event id | Avoids the classic sequential-PK hot-range problem on a high-write append-only log; tenant-scoped write distribution. |
| Vector index prefix columns | `(org_id, agent_id, embedding)` for episodic/semantic; `(org_id, status, embedding)` for procedural | One K-means tree per scope value; `status='active'` as a prefix column (not a partial-index predicate) because C-SPANN prefix filtering requires literal equality/IN index columns. |
| Bitemporal vs. MVCC time-travel | Explicit `valid_from`/`valid_to`/`recorded_at` columns, not `AS OF SYSTEM TIME` | CockroachDB's own MVCC history is bounded by the GC window (~25h) and meant for short-lived consistency, not unbounded belief-revision history. |
| Runbook representation | Structured JSONB `steps` with per-step safety gates (`reversible`, `requires_human_approval`, `verify`), plus separate matching surface (`trigger_desc`/embedding/`applicable_*`/`preconditions`) and track-record columns | Splits "how do I find this runbook" from "how do I safely run it" from "has it actually worked" — a flat blob can't support three-stage retrieval or a safety gate an agent's action loop can check without an LLM call. |
| Runbook versioning | `(org_id, agent_id, name, version)` composite identity, never mutate `steps` in place | Full audit trail of how a runbook evolved; supports draft→active→deprecated lifecycle and rollback if a promoted runbook underperforms. |
| Read consistency for recall | Default (leaseholder) reads on the hot recall-before-act path; follower reads reserved for UI/analytics only | Directly delivers the charter's 0ms read-your-write-staleness metric; the split must be explicit so `02` doesn't accidentally use follower reads on the decision path. |
| TTL / decay | Importance-weighted `ttl_expiration_expression` on `episodic_events`; no/long TTL on current facts and active runbooks; short fixed TTL on working memory | Declarative forgetting curve; consolidation job's importance bump is the mechanism that protects episodes that fed a fact/runbook, without needing cross-table TTL expressions (which CockroachDB doesn't support). |
| Multi-tenant homing | Schema supports `REGIONAL BY ROW` (§7.3) but is **not applied by default** in v1 | Charter marks this a stretch goal; applying it is a PK-changing migration, cheaper to do early than retrofit — flagged as a week-1/2 decision point, not deferred silently. |
| Operational table ownership | `01` defines an illustrative minimal slice; `05` owns the canonical version | Prevents two docs from independently inventing the SUM schema; the co-location *pattern* is what `01` must nail down, not every operational column. |

---

## C. Interfaces I expose / depend on

**Exposed (other docs consume these):**

- **Recall query shapes** (§4.1) — three-stage runbook match, single-stage episodic/semantic ANN — for
  `02`'s tool interface to call directly as SQL.
- **The co-location transaction pattern** (§3) — the canonical "recall informed an action, one commit"
  shape `02` should mirror for every remediation action.
- **The bitemporal transition pattern** (§2) and **consolidation write patterns** (§6) — the exact CTEs
  `02`/`03`'s consolidation job should execute; it reads `episodic_events`/`incidents`/`procedural_memory`
  as shown and writes `semantic_facts`/`procedural_memory`/`runbook_provenance` as shown.
- **`episodic_by_incident` index** — the query the console's memory-timeline panel (`06`) should use for
  the incident replay view.
- **Belief-history query** (§2) — powers any "why did the agent think X" explainability surface in `06`.
- **Changefeed on `episodic_events`, `incidents`** — the trigger `03` wires to Lambda for consolidation.
- **Session/working-memory upsert contract** (§1.4) — the idempotent scratchpad pattern `02`'s agent
  loop should call every turn.

**Depended on (this doc assumes/needs from others):**

- **`02` (agent orchestration):** the tool-name enum and argument schema referenced inside
  `procedural_memory.steps[].tool`/`args` — `01` owns the JSONB envelope, `02` owns what's valid inside
  it. Also: confirmation of the embedding model/dimension (§4.2/B) before data ingestion begins, since
  `01` can't finalize `VECTOR(n)` without it.
- **`03` (AWS infrastructure):** changefeed sink wiring (the webhook endpoint referenced in the DDL is a
  placeholder), IAM for `CREATE EXTERNAL CONNECTION`, and provisioning `feature.vector_index.enabled`
  before this schema is applied.
- **`04` (CockroachDB deployment/resilience):** the pinned cluster version (must be v25.3+ for
  `vector_cosine_ops` to be index-accelerated — otherwise fall back per §4.2), the actual multi-region
  topology and survival goal (`SURVIVE REGION FAILURE`, 3+ regions) that makes §7.3's homing columns
  meaningful, and the C-SPANN cluster settings/tuning knobs referenced in §4.4.
- **`05` (data & evaluation):** the canonical/extended operational schema beyond §3's illustrative slice;
  the seeded incident corpus that recall@k tuning (§4.4) and MTTR-delta measurement (charter §8) are
  validated against; the actual `event_type`/`predicate`/`applicable_error_signatures` vocabularies used
  by the dataset (this doc defines the columns, `05` defines the values).

---

## D. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Vector index backfill blocks all writes on a non-empty table (known C-SPANN limitation, `02`-vector-search-and-cspann §6) | Adding a vector index to a pre-populated `episodic_events` mid-build would stall the agent. | Create every vector index at table-creation time (empty table), before any seed/demo data is loaded. Never retrofit a vector index onto a populated table outside a maintenance window. |
| `feature.vector_index.enabled` is an opt-in cluster setting even on versions where the preview banner is gone | Silent failure ("no such index type") if forgotten during provisioning. | Bake the `SET CLUSTER SETTING` into `03`'s provisioning script as a hard prerequisite, not an app-level assumption; called out at the top of the starter DDL (§A). |
| TTL expiration expressions can't reference other tables — can't directly encode "never expire an episode belonging to a still-open incident" | Low-importance episodes for a long-running incident could theoretically be swept early. | Non-issue at Postmortem's timescale (30-day floor vs. hour/day-scale incidents); consolidation job's importance bump (§5/§6) is the actual safety net once an episode is known to matter. |
| Runbook `steps` JSONB drifts from `02`'s actual tool registry (renamed/removed tool, changed arg shape) | A matched runbook could reference a tool the agent's current build doesn't support, breaking execution at the worst moment (mid-incident). | Runbook versioning (§1.3/B) already isolates this — a schema/tool mismatch shows up as an execution-time validation failure on a specific version, not silent corruption; recommend `02` validate `steps[].tool` against its live registry before offering a runbook as a candidate, and deprecate versions that fail validation. |
| Over-narrow vector-index prefix scoping on `procedural_memory` (e.g., adding `service_id` as a hard prefix) would fragment the runbook corpus and miss cross-service-applicable runbooks | Reduced recall for genuinely reusable runbooks (e.g., "restart pod" applies everywhere). | Keep the vector index prefix minimal (`org_id, status`); push service/error-signature applicability to stage-2 structured filtering (§4.1), not the index prefix. |
| Embedding model/dimension chosen late or changed mid-build | Every `VECTOR(n)` column and its index would need to be dropped and rebuilt — expensive during a 4-week sprint. | Lock the decision in §4.2/B during week 1, before any real embeddings are written; flagged explicitly to `02`/`03` as a blocking cross-doc dependency. |
| Concurrent consolidation runs racing on the same fact's bitemporal transition | Both `40001` on the losing transaction — expected, not a correctness bug, given the `WHERE valid_to IS NULL` guard in the CTE. | Standard `40001` retry (server-auto-retried, since it's a single CTE statement); no special-casing needed. Document as expected retry pressure, not an incident. |
| Row/transaction size guardrails (`VECTOR` values <1MB, transaction payload well under CockroachDB's practical ~16MB limit) | Long raw tool-call transcripts or LLM traces stored directly in `content`/`steps` could blow these limits. | `episodic_events.content` is explicitly a summarized/truncated field with a `raw_ref` pointer to S3 for the full payload (§1.1); enforce the same discipline on any oversized `procedural_memory.steps` payload if a runbook's step list grows unusually large. |
| Applying `REGIONAL BY ROW` (§7.3) late, after data volume grows | PK-changing migration becomes expensive/risky under load. | If the multi-tenant stretch goal is likely to be pursued, do the homing migration in week 1–2 while tables are still small/empty, not week 4. |

---

## Charter success-metric mapping

| Charter metric (§8) | How this schema delivers it |
|---|---|
| Read-your-write staleness: 0ms | Default (leaseholder) reads on every hot recall path, never follower reads on the decision path (§4.3). |
| Region-failover RPO: 0 rows | Vector index partitions are ordinary KV rows replicated via Raft like any other table data (`02`-vector-search-and-cspann §5) — the same `SURVIVE REGION FAILURE` guarantee `04` configures at the cluster level covers memory tables automatically; §7.3 makes tables homeable if `04` applies `REGIONAL BY ROW`. |
| Region-failover RTO: <10s automatic | Inherited from CockroachDB's automatic range releasing/re-leasing — no failover script for memory specifically; cited average ~4.5s / worst-case <9s in `01`-architecture-and-resilience. |
| Memory-write + remediation atomicity: 1 ACID transaction | §3's co-location contract and worked example. |
| Vector recall quality: recall@k ≥95% | C-SPANN's 99%+ design target with headroom; tuning knobs in §4.4; empirical validation is `05`'s job against this schema's indexes. |
| Cross-agent memory visibility: no lag | Single store, single transaction boundary — no CDC/dual-write sync gap between "memory" and "operational" (or between two agent instances reading the same tables). |
| MTTR (simulated) with memory vs. cold | `incidents.opened_at`/`closed_at` and `episodic_events.occurred_at` give `05` the raw timestamps to compute the delta; the procedural-memory match+execute path (§4.1, §3) is the mechanism that should produce it. |
