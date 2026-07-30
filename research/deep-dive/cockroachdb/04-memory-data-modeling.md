# 04 — Modeling Agent Memory in CockroachDB

A practical reference for the schema patterns and CockroachDB features that make CockroachDB a viable
**system of record for agentic memory** — episodic, semantic, procedural, and working memory — with
native vector recall, bitemporal fact tracking, CDC-triggered consolidation, decay/forgetting via TTL,
and multi-region residency. Verified against CockroachDB **v25.2+ / v26.x** docs, July 2026.

---

## 0. Design principles before the DDL

1. **One store, not four.** Structured metadata, JSONB payloads, and vector embeddings live in the same
   row, in the same ACID transaction. No sync job between an operational DB and a separate vector DB.
2. **Scope everything.** Every memory row carries `org_id` (tenant), `agent_id`, and usually `user_id` /
   `session_id`. This is both an access-control boundary and — because CockroachDB range-splits on key
   ranges — a natural sharding/locality axis for multi-tenant write distribution.
3. **Avoid monotonic primary keys.** `SERIAL`/sequential PKs on a high-write append-only log (episodic
   memory is exactly that) create a single hot range. Lead composite keys with `org_id` or use
   `gen_random_uuid()`; see [designing-application-transactions](https://www.cockroachlabs.com/docs/stable/performance-best-practices-overview) guidance on key distribution.
4. **Never overwrite a fact — transition it.** Semantic memory needs bitemporal versioning so an agent
   can reason about *what it believed and when*, not just the current value.
5. **Memory decays.** Row-level TTL gives you a declarative "forgetting curve" instead of a cron job
   that runs `DELETE FROM ... WHERE`.

---

## 1. Modeling the four memory types

### 1.1 Episodic memory — the event log

Append-only record of what the agent observed/did. High write volume, read via vector similarity +
recency, scoped to org/agent/session.

```sql
CREATE TABLE episodic_events (
    event_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    user_id       UUID,
    session_id    UUID,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when the event happened
    event_type    STRING      NOT NULL,                  -- 'user_message','tool_call','observation','decision'
    content       STRING,                                 -- raw text (for LLM context re-injection)
    metadata      JSONB       NOT NULL DEFAULT '{}',      -- flexible: tool name, args, tokens, latency_ms...
    importance    FLOAT8      NOT NULL DEFAULT 0.5,       -- 0..1, drives TTL decay (see §4)
    embedding     VECTOR(1536),                            -- e.g. text-embedding-3-small
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Lead the PK with org_id: spreads a high-write log across ranges by tenant
    -- instead of hammering one hot range at the "end" of a global timeline.
    PRIMARY KEY (org_id, event_id),

    VECTOR INDEX (org_id, agent_id, embedding),  -- prefix-scoped ANN search
    INVERTED INDEX (metadata)                    -- GIN/inverted index for ad-hoc JSONB filters
);

-- Recency scan per session (keyset-paginated, see §6)
CREATE INDEX episodic_by_session ON episodic_events (org_id, session_id, occurred_at DESC)
  STORING (event_type, content);
```

`VECTOR(n)` requires an explicit dimension count, and CockroachDB's vector index (**C-SPANN**) currently
optimizes nearest-neighbor filtering only on **prefix columns** of the index — hence `(org_id, agent_id,
embedding)` rather than filtering on arbitrary predicates after the vector column. [Vector Indexes](https://www.cockroachlabs.com/docs/v25.2/vector-indexes)

Recall query:

```sql
SELECT event_id, occurred_at, content, metadata
FROM episodic_events
WHERE org_id = $1 AND agent_id = $2
ORDER BY embedding <-> $3::VECTOR(1536)
LIMIT 8;
```

### 1.2 Semantic memory — durable facts

Subject/predicate/object (or flattened attribute) facts the agent has learned, with confidence and
provenance. This is the table that needs **bitemporal** treatment — see §2 for the full pattern.

```sql
CREATE TABLE semantic_facts (
    fact_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    user_id       UUID,
    subject       STRING      NOT NULL,   -- e.g. 'user:1234' or 'account:acme-corp'
    predicate     STRING      NOT NULL,   -- e.g. 'prefers_language', 'billing_plan'
    object        JSONB       NOT NULL,   -- typed value, keeps schema flexible
    confidence    FLOAT8      NOT NULL DEFAULT 1.0,
    source        STRING,                 -- 'user_stated' | 'inferred' | 'tool:crm_sync'
    embedding     VECTOR(1536),

    valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- business time: when true in the world
    valid_to      TIMESTAMPTZ,                          -- NULL = currently believed true
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),   -- system time: when CRDB learned it
    superseded_by UUID REFERENCES semantic_facts (fact_id),

    PRIMARY KEY (org_id, subject, predicate, fact_id),
    VECTOR INDEX (org_id, agent_id, embedding)
);

-- Fast "what do we currently believe" lookups
CREATE INDEX semantic_current ON semantic_facts (org_id, subject, predicate)
  STORING (object, confidence) WHERE valid_to IS NULL;   -- partial index, current facts only
```

Partial indexes (`WHERE valid_to IS NULL`) keep the "current belief" index small even as history grows
unbounded. [Partial Indexes](https://www.cockroachlabs.com/docs/stable/partial-indexes)

### 1.3 Procedural memory — learned workflows / runbooks

Reusable action sequences the agent has distilled from experience (a mini "skill library"). Modeled as
versioned, JSONB-encoded step lists, retrievable by embedding the *intent* description.

```sql
CREATE TABLE procedural_memory (
    runbook_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    name          STRING      NOT NULL,
    version       INT         NOT NULL DEFAULT 1,
    status        STRING      NOT NULL DEFAULT 'active', -- 'draft'|'active'|'deprecated'
    trigger_desc  STRING      NOT NULL,                   -- natural-language "when to use this"
    steps         JSONB       NOT NULL,                   -- ordered list: [{"tool":..,"args":..}, ...]
    preconditions JSONB       NOT NULL DEFAULT '[]',
    success_rate  FLOAT8      NOT NULL DEFAULT 0,
    usage_count   INT8        NOT NULL DEFAULT 0,
    last_used_at  TIMESTAMPTZ,
    embedding     VECTOR(1536),                            -- embedding of trigger_desc, for intent match
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (org_id, agent_id, name, version),
    VECTOR INDEX (org_id, agent_id, embedding)
);

-- Retrieve the best-fit runbook for a new intent
SELECT runbook_id, name, steps, success_rate
FROM procedural_memory
WHERE org_id = $1 AND agent_id = $2 AND status = 'active'
ORDER BY embedding <-> $3::VECTOR(1536)
LIMIT 3;
```

Versioning via `(name, version)` rather than mutating `steps` in place preserves an audit trail of how a
runbook evolved — cheap insurance for debugging why an agent started behaving differently.

### 1.4 Working memory — session/scratchpad state

Short-lived, high-churn, TTL'd aggressively. Two complementary shapes: an **append-only turn log** (for
replay/debugging) and a **mutable scratchpad row** (for fast read-modify-write of "current plan/state").

```sql
CREATE TABLE session_turns (
    session_id    UUID        NOT NULL,
    turn_index    INT8        NOT NULL,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    role          STRING      NOT NULL,        -- 'user'|'assistant'|'tool'
    content       STRING,
    tool_calls    JSONB       NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, turn_index)
) WITH (ttl_expire_after = '24 hours');

CREATE TABLE session_state (
    session_id    UUID        NOT NULL PRIMARY KEY,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    user_id       UUID,
    scratchpad    JSONB       NOT NULL DEFAULT '{}',  -- current plan, open tool calls, working vars
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (ttl_expire_after = '4 hours');

-- Idempotent "upsert scratchpad" — safe to call every turn
UPSERT INTO session_state (session_id, org_id, agent_id, user_id, scratchpad, updated_at)
VALUES ($1, $2, $3, $4, $5, now());
```

`session_state` deliberately has **no vector index** — working memory is looked up by `session_id`, not
by similarity. Keep it cheap to write.

---

## 2. Bitemporal fact modeling: transition, don't overwrite

Two independent time axes matter for agent memory:

- **Valid time** (`valid_from`/`valid_to`) — when the fact was true *in the world*. Business-defined,
  can be backdated or future-dated (e.g., "plan changes on the 1st").
- **Transaction/system time** (`recorded_at`) — when CockroachDB itself learned the fact. Always
  monotonic, always `now()` at insert.

This is distinct from CockroachDB's own MVCC time travel (`AS OF SYSTEM TIME`), which only reaches back
through the garbage-collection window (default ~25h, tunable via `gc.ttlseconds`) and is meant for
short-lived consistency/follower-read use, not long-term historical reasoning. [Time-travel Queries](https://www.cockroachlabs.com/docs/stable/as-of-system-time) —
bitemporal columns are how you get *unbounded, queryable* history.

**Closing an old fact and opening a new one, atomically, in one implicit transaction:**

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

Because this is a single statement, it's an **implicit transaction** — server-side auto-retried on
`40001`, no client retry loop required, one round trip. This mirrors the CTE-over-multi-statement
pattern that consistently wins on CockroachDB under contention (see §6).

**Query "what do we believe now"**: `WHERE valid_to IS NULL` (served by the partial index in §1.2).

**Query "what did we believe about X on date D"**:

```sql
SELECT object, confidence, source
FROM semantic_facts
WHERE org_id = $1 AND subject = $2 AND predicate = $3
  AND valid_from <= $4 AND (valid_to IS NULL OR valid_to > $4)
ORDER BY recorded_at DESC
LIMIT 1;
```

**Query "show me the belief history / how this fact evolved"**:

```sql
SELECT object, confidence, valid_from, valid_to, recorded_at, source
FROM semantic_facts
WHERE org_id = $1 AND subject = $2 AND predicate = $3
ORDER BY recorded_at;
```

This is the pattern an agent uses to answer "why did you think X" or "when did that change" — it's
reasoning over the *shape of belief revision*, not just a point-in-time value.

---

## 3. Changefeeds: triggering async "sleep-time" consolidation

Sleep-time consolidation (compacting episodic events into semantic facts, decaying importance, distilling
runbooks) should run **outside** the hot write path. CockroachDB **changefeeds** stream row-level changes
to an external consumer, which is the trigger mechanism for that async job.

### Webhook sink → API Gateway → Lambda

Best fit for "call my consolidation service whenever new episodic events land":

```sql
CREATE CHANGEFEED FOR TABLE episodic_events, semantic_facts
INTO 'webhook-https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/consolidate'
WITH updated,
     resolved = '10s',
     min_checkpoint_frequency = '5s',
     webhook_sink_config = '{"Flush":{"Bytes":1048576,"Messages":100,"Frequency":"5s"}}';
```

- `updated` includes the change timestamp in each emitted record; `resolved` periodically emits a
  watermark so the consumer knows "everything up to time T has been delivered" — useful for batch
  consolidation windows.
- `webhook_sink_config` controls client-side batching (flush every 100 messages / 1 MB / 5s), which caps
  Lambda invocation rate.
- Webhook sinks require HTTPS. [Changefeed Sinks](https://www.cockroachlabs.com/docs/v26.2/changefeed-sinks), [CREATE CHANGEFEED](https://www.cockroachlabs.com/docs/stable/create-changefeed)

### Kafka / Amazon MSK sink → Lambda event-source mapping

Better fit at higher throughput, or if other consumers (analytics, replay tooling) also need the stream:

```sql
CREATE CHANGEFEED FOR TABLE episodic_events
INTO 'kafka://b-1.agentmemory.abcde.c2.kafka.us-east-1.amazonaws.com:9098?topic_prefix=agent_memory_&tls_enabled=true&sasl_enabled=true&sasl_mechanism=AWS_MSK_IAM'
WITH format = json, envelope = wrapped, resolved = '30s';
```

Wire a Lambda [MSK event source mapping](https://docs.aws.amazon.com/lambda/latest/dg/with-msk.html) to
the topic to invoke the consolidation function per batch.

### Cloud storage (S3) sink → S3 event notification → Lambda

Best for **batch/nightly** consolidation rather than near-real-time — cheap, no broker to run:

```sql
CREATE CHANGEFEED FOR TABLE episodic_events
INTO 's3://agent-memory-cdc-bucket/episodic?AWS_REGION=us-east-1'
WITH format = json, envelope = wrapped, resolved = '1h', partition_format = daily;
```

Configure an S3 `ObjectCreated` event notification on the bucket to invoke a consolidation Lambda per
landed file — a natural fit for "run sleep-time consolidation once a day."

### Don't embed credentials in the URI — use external connections

```sql
CREATE EXTERNAL CONNECTION memory_consolidation_webhook
  AS 'webhook-https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/consolidate';

CREATE CHANGEFEED FOR TABLE episodic_events
INTO 'external://memory_consolidation_webhook'
WITH updated, resolved = '10s';
```

Supported sink schemes overall: `kafka://`, MSK (`kafka://` + IAM SASL), `confluent-cloud://`,
`gcpubsub://`, `azure-event-hub://`, `pulsar://` (preview), cloud storage (`s3://`, `gs://`, `azure://`),
and `webhook-https://`. [Changefeed Sinks](https://www.cockroachlabs.com/docs/v26.2/changefeed-sinks)

**Design note:** don't changefeed the `session_state`/`session_turns` working-memory tables — they're
noise for a consolidation job. Only changefeed `episodic_events` (raw material to consolidate) and
`semantic_facts`/`procedural_memory` (so downstream systems — dashboards, other agents — see belief
updates as they happen).

---

## 4. Row-level TTL: memory decay and forgetting

CockroachDB's **Row-Level TTL** runs a background job that deletes expired rows automatically — a
declarative replacement for a cron-job `DELETE`. [Row-Level TTL](https://www.cockroachlabs.com/docs/v26.2/row-level-ttl)

**Fixed decay** (working memory, §1.4 already shown):

```sql
ALTER TABLE session_state SET (ttl_expire_after = '4 hours');
```

**Importance-weighted decay** — a real "forgetting curve" for episodic memory: important events survive
a year, routine chatter is forgotten in 30 days:

```sql
ALTER TABLE episodic_events SET (
    ttl_expiration_expression =
      "CASE WHEN importance >= 0.8 THEN occurred_at + INTERVAL '365 days'
            WHEN importance >= 0.4 THEN occurred_at + INTERVAL '90 days'
            ELSE occurred_at + INTERVAL '30 days' END",
    ttl_job_cron = '@daily'
);
```

`ttl_expiration_expression` must evaluate to `TIMESTAMPTZ`; `ttl_job_cron` controls how often the
background sweep runs (default hourly). [Row-Level TTL](https://www.cockroachlabs.com/docs/v26.2/row-level-ttl)

**Should TTL deletes flow through the changefeed?** By default, yes — TTL deletes are ordinary deletes and
will appear in a changefeed on that table. If you *want* the consolidation job to know "this memory was
forgotten, remove it from any derived index," leave that on. If TTL churn is just noise for your consumer,
suppress it per table:

```sql
ALTER TABLE episodic_events SET (ttl_disable_changefeed_replication = 'true');
```

---

## 5. Multi-region homing: `REGIONAL BY ROW`

For data residency (a user's memory must stay in their jurisdiction) and latency (an agent should read/
write its own user's memory from the nearest region), pin memory rows to a home region with `REGIONAL BY
ROW`. [Table Localities](https://www.cockroachlabs.com/docs/stable/table-localities)

```sql
CREATE DATABASE agent_memory;
ALTER DATABASE agent_memory PRIMARY REGION 'us-east-1';
ALTER DATABASE agent_memory ADD REGION 'eu-west-1';
ALTER DATABASE agent_memory ADD REGION 'ap-southeast-1';
ALTER DATABASE agent_memory SURVIVE REGION FAILURE;  -- or SURVIVE ZONE FAILURE for lower write latency

CREATE TABLE episodic_events (
    event_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    home_region   crdb_internal_region NOT NULL,   -- explicit, from the org's registered residency
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    content       STRING,
    metadata      JSONB       NOT NULL DEFAULT '{}',
    embedding     VECTOR(1536),

    PRIMARY KEY (home_region, org_id, event_id)
) LOCALITY REGIONAL BY ROW AS home_region;
```

**Important nuance:** don't default the region column to `gateway_region()` for a compliance-sensitive
memory store — `gateway_region()` reflects the *connecting node's* locality, not necessarily the tenant's
data-residency requirement (an EU user could be served, transiently, by a US gateway node during
failover/routing). Set `home_region` explicitly at write time from the org's stored residency setting,
and only fall back to `gateway_region()` as a default for non-regulated deployments.

`REGIONAL BY ROW` gives ~2-5ms local reads/writes per region and ~20ms local write latency, vs. 50-150ms+
if a request has to cross regions. `SURVIVE REGION FAILURE` requires 3+ regions and adds cross-region
consensus latency in exchange for zero data loss on a full region outage; `SURVIVE ZONE FAILURE` is
cheaper but only survives an AZ-level failure. [Multi-Region Overview](https://www.cockroachlabs.com/docs/stable/multiregion-overview)

Reference (non-row-affine) data — global runbook templates shared across all tenants, for example — is a
better fit for `GLOBAL` tables (fast reads everywhere, writes from the primary region) than `REGIONAL BY
ROW`. [Global Tables](https://www.cockroachlabs.com/docs/stable/global-tables)

---

## 6. Transactions & performance for agent workloads

Agent memory writes are **small, frequent, and bursty** (one row per tool call/turn), while recall reads
are **latency-sensitive but staleness-tolerant** (an 8-vector similarity search doesn't need to see writes
from 200ms ago). That asymmetry drives the pattern choices below.

- **Implicit transactions for single-row writes.** Every episodic-event insert and working-memory upsert
  is a single statement — let CockroachDB auto-retry it server-side, no `BEGIN`/`COMMIT`, no client retry
  loop needed.
- **Explicit transactions only for multi-statement invariants** — and prefer collapsing them into a
  single CTE statement (the bitemporal transition in §2 is the canonical example) so it *stays* an
  implicit, auto-retried transaction instead of a multi-round-trip explicit one.
- **Client-side retry with backoff for anything that must stay multi-statement:**

  ```python
  import random, time

  def with_retry(conn, fn, max_attempts=5):
      backoff = 0.1
      for attempt in range(max_attempts):
          try:
              with conn.transaction():
                  return fn(conn)
          except SerializationFailure:
              if attempt == max_attempts - 1:
                  raise
              time.sleep(backoff + random.uniform(0, 0.1))
              backoff = min(backoff * 2, 2.0)
  ```

  Retry on SQLSTATE `40001` (serialization failure); treat `40003` (ambiguous commit) as non-idempotent-
  unsafe to blindly replay; propagate everything else.
- **Connection pooling.** Agent workloads are often invoked from Lambda or similar bursty compute, which
  is exactly the failure mode connection pools exist to prevent (connection storms). Put a pooler (RDS
  Proxy-style, or PgBouncer) in front if invocations fan out from serverless compute; size a long-lived
  service's pool at roughly `4 × vCPUs` per instance.
- **Prepared statements.** The episodic-insert and working-memory-upsert shapes repeat on every single
  turn — prepare them once per connection and bind params, rather than re-planning identical SQL text
  every call.
- **Keyset pagination for history/replay reads**, not `OFFSET`:

  ```sql
  SELECT event_id, occurred_at, content
  FROM episodic_events
  WHERE org_id = $1 AND session_id = $2 AND occurred_at < $3   -- cursor from last row
  ORDER BY occurred_at DESC
  LIMIT 50;
  ```

- **Follower reads for recall.** Semantic/episodic recall queries (vector search, "what do we know about
  X") almost never need linearizable freshness — read from the closest replica instead of the leaseholder:

  ```sql
  SELECT event_id, content
  FROM episodic_events
  AS OF SYSTEM TIME follower_read_timestamp()
  WHERE org_id = $1 AND agent_id = $2
  ORDER BY embedding <-> $3::VECTOR(1536)
  LIMIT 8;
  ```

  Default follower-read staleness is driven by `kv.closed_timestamp.target_duration` (+ propagation
  slack), ~4.2s by default — tune down if the workload wants fresher-but-still-local reads.
  [Follower Reads](https://www.cockroachlabs.com/docs/stable/follower-reads)
- **Session guardrails** during development catch a missing `WHERE` before it becomes an incident:
  `SET transaction_rows_read_err = 10000; SET transaction_rows_written_err = 1000;`
- **Respect the practical ~16MB transaction payload limit.** Keep individual memory rows well under 1MB
  (a raw tool-call payload or long transcript chunk should be truncated/summarized before storage, or
  pushed to S3 with a reference column) and total transaction payload under a few MB.

Full treatment: [Transactions](https://www.cockroachlabs.com/docs/stable/transactions), [Advanced Client-Side Transaction Retries](https://www.cockroachlabs.com/docs/stable/advanced-client-side-transaction-retries), [Performance Best Practices](https://www.cockroachlabs.com/docs/stable/performance-best-practices-overview).

---

## 7. PostgreSQL compatibility — low-friction Python/TypeScript integration

CockroachDB speaks the PostgreSQL wire protocol (pgwire v3), so most standard drivers/ORMs work with
little or no adaptation. [PostgreSQL Compatibility](https://www.cockroachlabs.com/docs/v26.2/postgresql-compatibility), [Install a Driver or ORM](https://www.cockroachlabs.com/docs/stable/install-client-drivers)

| Ecosystem | What works | Notes |
|---|---|---|
| Python | `psycopg2` / `psycopg3` | Standard wire-protocol connection, no special driver needed. |
| Python ORM | SQLAlchemy + `sqlalchemy-cockroachdb` dialect | Cockroach Labs maintains a dialect package that smooths over CRDB/PG differences (e.g., retry helpers). |
| TypeScript/Node | `pg`, `postgres.js` | Wire-compatible, works out of the box. |
| TypeScript ORM | Prisma (`provider = "cockroachdb"`) | Supported natively in `schema.prisma`; avoid the `@prisma/adapter-pg` driver adapter path — it's PG-specific and known incompatible with the CockroachDB provider. Use Prisma's built-in CockroachDB connector instead. |
| Vector/pgvector ecosystem | LangChain, LlamaIndex, `pgvector-python` integrations that emit `<->` | CockroachDB's `VECTOR` type and operator interface are **designed to mirror pgvector's API** (same `<->` L2-distance operator), so tools built against pgvector largely point-and-shoot. **Caveat:** CockroachDB's vector index currently accelerates **L2 distance only** — no native `<=>` cosine or `<#>` inner-product operators, which some pgvector-based tools default to. Workaround: normalize embeddings to unit length before storing; for unit vectors, L2-distance ranking is monotonically equivalent to cosine-similarity ranking. Also: `VECTOR` is a **native built-in type**, not a `CREATE EXTENSION vector` add-on — drop that step when porting a Postgres/pgvector tutorial. |

Sources: [PostgreSQL Compatibility](https://www.cockroachlabs.com/docs/v26.2/postgresql-compatibility), [Vector Search with pgvector API compatibility](https://www.cockroachlabs.com/blog/vector-search-pgvector-cockroachdb/), [Vector Indexes](https://www.cockroachlabs.com/docs/v25.2/vector-indexes)

---

## A. Proposed starter schema (full DDL)

```sql
-- ============================================================
-- Agent Memory Store — starter schema
-- Multi-region, multi-tenant, vector-enabled, bitemporal, TTL'd
-- ============================================================

CREATE DATABASE IF NOT EXISTS agent_memory;
ALTER DATABASE agent_memory PRIMARY REGION 'us-east-1';
ALTER DATABASE agent_memory ADD REGION 'eu-west-1';
ALTER DATABASE agent_memory SURVIVE ZONE FAILURE;   -- switch to SURVIVE REGION FAILURE for stricter DR

USE agent_memory;

-- ---------- Episodic memory: event log ----------
CREATE TABLE episodic_events (
    event_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    user_id       UUID,
    session_id    UUID,
    home_region   crdb_internal_region NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type    STRING      NOT NULL,
    content       STRING,
    metadata      JSONB       NOT NULL DEFAULT '{}',
    importance    FLOAT8      NOT NULL DEFAULT 0.5,
    embedding     VECTOR(1536),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (home_region, org_id, event_id),
    VECTOR INDEX (org_id, agent_id, embedding),
    INVERTED INDEX (metadata),
    INDEX episodic_by_session (org_id, session_id, occurred_at DESC) STORING (event_type, content)
) LOCALITY REGIONAL BY ROW AS home_region
  WITH (
    ttl_expiration_expression =
      "CASE WHEN importance >= 0.8 THEN occurred_at + INTERVAL '365 days'
            WHEN importance >= 0.4 THEN occurred_at + INTERVAL '90 days'
            ELSE occurred_at + INTERVAL '30 days' END",
    ttl_job_cron = '@daily'
  );

-- ---------- Semantic memory: bitemporal facts ----------
CREATE TABLE semantic_facts (
    fact_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    user_id       UUID,
    home_region   crdb_internal_region NOT NULL,
    subject       STRING      NOT NULL,
    predicate     STRING      NOT NULL,
    object        JSONB       NOT NULL,
    confidence    FLOAT8      NOT NULL DEFAULT 1.0,
    source        STRING,
    embedding     VECTOR(1536),

    valid_from    TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to      TIMESTAMPTZ,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by UUID,

    PRIMARY KEY (home_region, org_id, subject, predicate, fact_id),
    VECTOR INDEX (org_id, agent_id, embedding),
    INDEX semantic_current (org_id, subject, predicate) STORING (object, confidence)
      WHERE valid_to IS NULL
) LOCALITY REGIONAL BY ROW AS home_region;

-- ---------- Procedural memory: learned runbooks ----------
CREATE TABLE procedural_memory (
    runbook_id    UUID        NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    home_region   crdb_internal_region NOT NULL,
    name          STRING      NOT NULL,
    version       INT         NOT NULL DEFAULT 1,
    status        STRING      NOT NULL DEFAULT 'active',
    trigger_desc  STRING      NOT NULL,
    steps         JSONB       NOT NULL,
    preconditions JSONB       NOT NULL DEFAULT '[]',
    success_rate  FLOAT8      NOT NULL DEFAULT 0,
    usage_count   INT8        NOT NULL DEFAULT 0,
    last_used_at  TIMESTAMPTZ,
    embedding     VECTOR(1536),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (home_region, org_id, agent_id, name, version),
    VECTOR INDEX (org_id, agent_id, embedding)
) LOCALITY REGIONAL BY ROW AS home_region;

-- ---------- Working memory: session state (short TTL) ----------
CREATE TABLE session_turns (
    session_id    UUID        NOT NULL,
    turn_index    INT8        NOT NULL,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    role          STRING      NOT NULL,
    content       STRING,
    tool_calls    JSONB       NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, turn_index)
) WITH (ttl_expire_after = '24 hours');

CREATE TABLE session_state (
    session_id    UUID        NOT NULL PRIMARY KEY,
    org_id        UUID        NOT NULL,
    agent_id      UUID        NOT NULL,
    user_id       UUID,
    scratchpad    JSONB       NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (ttl_expire_after = '4 hours');

-- ---------- CDC: stream new episodic events + fact changes to a consolidation service ----------
CREATE EXTERNAL CONNECTION memory_consolidation_webhook
  AS 'webhook-https://<api-id>.execute-api.us-east-1.amazonaws.com/prod/consolidate';

CREATE CHANGEFEED FOR TABLE episodic_events, semantic_facts
INTO 'external://memory_consolidation_webhook'
WITH updated,
     resolved = '10s',
     min_checkpoint_frequency = '5s',
     webhook_sink_config = '{"Flush":{"Bytes":1048576,"Messages":100,"Frequency":"5s"}}';
```

---

## B. Feature-to-need map

| Agent-memory requirement | CockroachDB feature | Why it fits |
|---|---|---|
| Semantic recall by similarity | `VECTOR(n)` type + `VECTOR INDEX` (**C-SPANN**) | Native ANN index co-located with structured data — one ACID store, no separate vector DB, no sync lag. [Vector Indexes](https://www.cockroachlabs.com/docs/v25.2/vector-indexes) |
| Flexible/evolving metadata per memory row | `JSONB` + `INVERTED INDEX` (GIN) | Schema-flexible payloads (tool args, provenance) without a migration per new field; indexed ad-hoc filtering. [Inverted Indexes](https://www.cockroachlabs.com/docs/v26.2/inverted-indexes) |
| Multi-tenant / multi-agent isolation | `org_id`/`agent_id`/`user_id`/`session_id` scope columns in every key & index | Access-control boundary *and* a natural key-distribution axis, avoiding hotspots on a single global log. |
| "What did we believe, and when" | Bitemporal columns (`valid_from`/`valid_to`/`recorded_at`) + CTE-based transition | Full belief-revision history, unbounded — beyond CRDB's own short MVCC/`AS OF SYSTEM TIME` GC window. |
| Async sleep-time consolidation trigger | **Changefeeds** (webhook / Kafka-MSK / S3 sinks) | Row-level CDC stream to Lambda without polling; `resolved`/watermarks give the consumer a consistent batch boundary. [CREATE CHANGEFEED](https://www.cockroachlabs.com/docs/stable/create-changefeed) |
| Memory decay / forgetting | **Row-Level TTL** (`ttl_expire_after` / `ttl_expiration_expression`) | Declarative, importance-weighted expiration via background job — no cron-based delete sweep to operate. [Row-Level TTL](https://www.cockroachlabs.com/docs/v26.2/row-level-ttl) |
| Data residency / low-latency per-tenant access | `REGIONAL BY ROW` + `crdb_internal_region` | Pins each tenant's rows to a home region for compliance and ~2-5ms local reads, without hand-managed partitioning. [Table Localities](https://www.cockroachlabs.com/docs/stable/table-localities) |
| Cheap, frequent, staleness-tolerant recall reads | **Follower reads** (`AS OF SYSTEM TIME follower_read_timestamp()`) | Serves recall queries from the nearest replica instead of the leaseholder — lower latency, no extra cluster load. [Follower Reads](https://www.cockroachlabs.com/docs/stable/follower-reads) |
| High write-throughput memory logging without retries | Implicit (single-statement) transactions, CTE-collapsed multi-step writes | Server-side auto-retry on `40001`, one round trip, no client retry loop for the common case. |
| Atomic multi-step consolidation writes | Explicit transaction + client-side exponential-backoff retry | SERIALIZABLE isolation is CRDB's default — retry loops are a normal, expected part of correct usage under contention. |
| Low-friction app-layer integration | PostgreSQL wire protocol compatibility | `psycopg`, SQLAlchemy, `pg`/`postgres.js`, Prisma all work with the standard connector; pgvector-API-compatible `<->` operator eases porting embedding pipelines. [PostgreSQL Compatibility](https://www.cockroachlabs.com/docs/v26.2/postgresql-compatibility) |

---

### Sources

- [Vector Indexes](https://www.cockroachlabs.com/docs/v25.2/vector-indexes)
- [Introducing Distributed Vector Indexing to CockroachDB](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/)
- [Real-Time Indexing for Billions of Vectors with CockroachDB (C-SPANN)](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/)
- [Introducing Vector Search with pgvector in CockroachDB](https://www.cockroachlabs.com/blog/vector-search-pgvector-cockroachdb/)
- [Generalized Inverted Indexes](https://www.cockroachlabs.com/docs/v26.2/inverted-indexes)
- [CREATE CHANGEFEED](https://www.cockroachlabs.com/docs/stable/create-changefeed)
- [Changefeed Sinks](https://www.cockroachlabs.com/docs/v26.2/changefeed-sinks)
- [Create and Configure Changefeeds](https://www.cockroachlabs.com/docs/stable/create-and-configure-changefeeds)
- [Batch Delete Expired Data with Row-Level TTL](https://www.cockroachlabs.com/docs/v26.2/row-level-ttl)
- [Table Localities](https://www.cockroachlabs.com/docs/stable/table-localities)
- [Multi-Region Capabilities Overview](https://www.cockroachlabs.com/docs/stable/multiregion-overview)
- [Global Tables](https://www.cockroachlabs.com/docs/stable/global-tables)
- [Follower Reads](https://www.cockroachlabs.com/docs/stable/follower-reads)
- [Time-travel Queries (AS OF SYSTEM TIME)](https://www.cockroachlabs.com/docs/stable/as-of-system-time)
- [Transactions](https://www.cockroachlabs.com/docs/stable/transactions)
- [Advanced Client-Side Transaction Retries](https://www.cockroachlabs.com/docs/stable/advanced-client-side-transaction-retries)
- [Performance Best Practices Overview](https://www.cockroachlabs.com/docs/stable/performance-best-practices-overview)
- [Partial Indexes](https://www.cockroachlabs.com/docs/stable/partial-indexes)
- [PostgreSQL Compatibility](https://www.cockroachlabs.com/docs/v26.2/postgresql-compatibility)
- [Install a Driver or ORM Framework](https://www.cockroachlabs.com/docs/stable/install-client-drivers)
