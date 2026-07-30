# Competitors: Distributed SQL & Cloud-Native Memory Stores

**Scope:** Objective reference on the databases and managed memory services that compete with CockroachDB as the backend for *agentic memory* on AWS. "Agentic memory" here means the persistent store an AI agent writes to constantly (conversation events, extracted facts, embeddings) and reads from with low latency, ideally co-located with the agent's operational/business data so there is one consistent system of record.

**What matters for this workload:**
- **Constant small writes** (every turn/event) with low latency and high durability.
- **Consistency between memory and operational data** — if the agent's memory and the app's transactional data disagree, the agent acts on stale/incorrect state.
- **Vector search** for semantic recall (RAG over prior interactions).
- **Resilience** — RPO (data loss window) and RTO (recovery time) on node/zone/region failure.
- **Operational simplicity** — one store vs. stitching a vector DB + a session store + a relational DB.

Date of research: 2026-07-30. Sources are cited inline.

---

## 1. PostgreSQL + pgvector (single-node baseline)

Vanilla PostgreSQL with the [`pgvector`](https://github.com/pgvector/pgvector) extension is the de-facto starting point for agent memory and RAG. You get relational tables, JSONB, transactions, and vector columns (`vector` type, IVFFlat and HNSW indexes) in one engine, queryable with plain SQL.

- **Distributed or single-node?** Single-node. Scaling is vertical (bigger box) plus read replicas. There is no built-in horizontal write scale or automatic sharding.
- **Multi-region consistency & auto-failover.** None natively. HA is bolted on (Patroni, streaming replication, a managed provider). Replicas are asynchronous, so a failover has a non-zero RPO (seconds of potential data loss) and RTO depends on the tooling. Cross-region is async and eventually consistent.
- **Vector support.** Yes — pgvector is mature (HNSW added in 0.5.0; 0.8.0 improved filtering and query planning per [AWS's pgvector 0.8.0 write-up](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/)).
- **SQL / Postgres-compatible?** It *is* Postgres.
- **Managed?** Only if you use a provider (RDS, Neon, Supabase, etc.); self-hosted otherwise.
- **Fit for agent memory.** Excellent developer experience and single-store simplicity for a **single-node/single-region** app. The ceiling is scale and resilience: constant agent writes on a hot single primary, no auto-failover with RPO=0, and vector-index build/memory cost that degrades past tens of millions of embeddings (a point [Yugabyte](https://www.yugabyte.com/blog/agentic-ai-and-extensible-vector-search/) and others make repeatedly). It is the honest baseline every distributed competitor — including CockroachDB — measures itself against.

---

## 2. Amazon Aurora (PostgreSQL) + pgvector, and Aurora DSQL

Two very different AWS products live here.

### Aurora PostgreSQL + pgvector
Managed PostgreSQL with a distributed storage layer (6-way replicated across 3 AZs). Supports [`pgvector` 0.8.0](https://aws.amazon.com/about-aws/whats-new/2025/04/pgvector-0-8-0-aurora-postgresql/), and AWS publishes production guidance for [running pgvector on Aurora](https://aws.amazon.com/blogs/database/running-pgvector-in-production-on-amazon-aurora-postgresql/). It is one of the built-in vector-store backends for Bedrock Knowledge Bases.

- **Distributed?** Storage is distributed across 3 AZs in one region; **compute is a single writer** (one primary). Not a shared-nothing multi-writer system.
- **Multi-region & failover.** In-region failover to a replica is fast. For multi-region you use **Aurora Global Database**: replication is **asynchronous**, so RPO is "typically measured in seconds" and a promoted region takes read/write "in under a minute" ([AWS global DB DR docs](https://aws.amazon.com/blogs/database/cross-region-disaster-recovery-using-amazon-aurora-global-database-for-amazon-aurora-postgresql/)). *Managed planned* failover can be RPO=0, but *unplanned* region loss is not.
- **Vector / SQL / managed.** Yes / full Postgres / fully managed.
- **Fit.** Strong single-region choice: full Postgres, mature pgvector, one store for memory + operational data. Weakness for a global agent is the single-writer + async multi-region model — you don't get active-active writes with zero-RPO region survival.

### Aurora DSQL (AWS's distributed Postgres)
Aurora DSQL is AWS's ground-up **distributed, serverless, active-active** SQL database, [GA on 2025-05-27](https://aws.amazon.com/about-aws/whats-new/2025/05/amazon-aurora-dsql-generally-available). It disaggregates compute, storage, and a transaction Journal; uses MVCC with optimistic concurrency deferring coordination to commit time (see the [Aurora DSQL paper](https://arxiv.org/abs/2607.13276) and [design guide](https://hidekazu-konishi.com/entry/amazon_aurora_dsql_design_decision_guide.html)).

- **Distributed & multi-region.** Yes — active-active, **multi-region strong consistency**: reads/writes to any regional endpoint are strongly consistent. Designed for **99.99% single-region / 99.999% multi-region** availability with automated failure recovery.
- **Vector support.** **No pgvector.** Multiple sources confirm DSQL does not support pgvector (it supports only a subset of PostgreSQL 16, no extensions like PostGIS, and [no foreign keys](https://www.yugabyte.com/blog/aurora-dsql-compared-to-yugabytedb/); see also [Andrew Baker's deep dive](https://andrewbaker.ninja/2025/11/19/amazon-aurora-dsql-performance-limits-architecture/)). This is the decisive gap for agent memory: **you cannot store embeddings + do semantic search in DSQL today.**
- **SQL / managed.** Postgres-*compatible* (subset) / fully serverless-managed.
- **Fit.** Architecturally the closest AWS-native answer to CockroachDB (distributed, strongly consistent, multi-region, Postgres-flavored). But the **missing vector support and the missing foreign keys / extension ecosystem** mean it is *not* a single-store agent-memory solution in 2026 — you'd bolt on a separate vector store, which reintroduces the two-system consistency problem CockroachDB avoids. (Note: "Aurora PostgreSQL Limitless" was AWS's earlier sharding product; DSQL is the current distributed-Postgres direction.)

---

## 3. Neon (serverless Postgres) + pgvector

[Neon](https://neon.com/docs/introduction/read-replicas) is serverless PostgreSQL with storage/compute separation and scale-to-zero. Popular for AI apps because it's cheap, branchable, and Postgres.

- **Distributed?** Single primary per project; storage is a custom disaggregated layer (Safekeepers use Paxos for WAL durability), but it is **not a multi-writer distributed SQL** engine.
- **Multi-region & consistency.** Read replicas are **asynchronous and eventually consistent**, and **same-region only** — Neon does not offer true cross-region replicas; you'd stand up a second project and use logical replication ([Neon read replicas](https://neon.com/docs/introduction/read-replicas), [same-region replica announcement](https://neon.tech/blog/introducing-same-region-read-replicas-to-serverless-postgres)). No zero-RPO multi-region failover.
- **Vector / SQL / managed.** pgvector supported ([Neon as a vector DB](https://cookbook.openai.com/examples/vector_databases/neon/readme)) / full Postgres / fully managed serverless.
- **Fit.** Great DX for a single-region agent, especially prototypes and per-tenant branching. Not AWS-native in the "runs inside your AWS account" sense (it's a separate SaaS, though hosted on AWS regions). Same resilience ceiling as single-node Postgres for a global, always-writing agent.

---

## 4. YugabyteDB (distributed SQL competitor)

[YugabyteDB](https://github.com/yugabyte/yugabyte-db) is an open-source, PostgreSQL-compatible, shared-nothing distributed SQL database — CockroachDB's most direct open-source peer. It reuses the actual PostgreSQL query layer on top of a distributed storage engine (DocDB) with Raft replication.

- **Distributed & multi-region.** Yes — shared-nothing, synchronous Raft replication, tunable multi-region topologies (including geo-partitioning). Region survival with strong consistency is a core feature.
- **Vector support.** Yes — [distributed vector indexing powered by USearch with HNSW](https://www.yugabyte.com/blog/yugabytedb-vector-indexing-architecture/), exposed through the pgvector interface. Yugabyte reports [testing to 100M vectors with ms-range latency and a design target of tens of billions](https://www.yugabyte.com/blog/agentic-ai-and-extensible-vector-search/), and publishes [multi-agent memory examples](https://www.yugabyte.com/blog/multi-agent-ai-with-yugabytedb-vector/).
- **SQL / managed.** High Postgres compatibility (reuses PG upper half) / managed via YugabyteDB Aeon or self-hosted; runs on AWS but is not an AWS first-party service.
- **Fit.** Very strong: distributed, strongly consistent, Postgres-compatible, native distributed vectors, single store for memory + operational data — essentially the same value proposition as CockroachDB. It is the sharpest *architectural* rival (see the head-to-head section below).

---

## 5. Google Cloud Spanner (+ vector) and AlloyDB

Google's two contenders — relevant as architecture peers even though the team is on AWS.

### Cloud Spanner
Globally distributed, externally consistent (TrueTime) relational database; horizontal scale across regions/continents.
- **Distributed & multi-region.** Yes — strong/external consistency globally, synchronous replication, automatic failover. This is the original "distributed SQL with strong multi-region consistency" system.
- **Vector.** Yes — [KNN exact and ANN search](https://docs.cloud.google.com/spanner/docs/find-k-nearest-neighbors) with `COSINE_DISTANCE()`/`EUCLIDEAN_DISTANCE()`/`DOT_PRODUCT()`, plus vector search over Spanner Graph.
- **SQL / managed.** GoogleSQL and a PostgreSQL dialect / fully managed. **Not Postgres-native**, **not AWS.**

### AlloyDB
Google's PostgreSQL-compatible engine with an analytics/columnar accelerator.
- **Vector.** pgvector plus Google's **[ScaNN index](https://cloud.google.com/blog/products/databases/how-scann-for-alloydb-vector-search-compares-to-pgvector-hnsw)**, which Google positions as faster index builds / smaller memory footprint than pgvector HNSW at large scale, with inline filtering ([AlloyDB ScaNN digest](https://medium.com/google-cloud/google-cloud-database-digest-alloydbs-scann-vector-index-unifies-your-data-ai-aug-22th-2025-3dc2eda8a345)).
- **Distributed & multi-region.** Primary + read pool in-region; **cross-region replication** with automated failover, and *switchover* gives RPO=0 for planned events ([cross-region replication](https://docs.cloud.google.com/alloydb/docs/cross-region-replication/about-cross-region-replication)). It is single-writer (not active-active multi-writer like Spanner/DSQL/CockroachDB).
- **SQL / managed.** Full Postgres-compatible / fully managed. **Not AWS.**

**Fit.** Both are strong single-store options *on GCP*. They matter to this project as proof that "distributed SQL + native vectors + one system of record" is the direction the whole industry is converging on — but neither is deployable as an AWS-native service, which is a hard constraint here.

---

## 6. MongoDB (Atlas) as agent memory

[MongoDB Atlas](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/) is a managed document database with integrated Atlas Vector Search. It has invested heavily in the agent-memory narrative.

- **Distributed & multi-region.** Yes — replica sets + sharding; multi-region deployments available. Consistency is tunable (read/write concerns); default cross-region is not the same as synchronous strong-consistency SQL, and multi-document ACID transactions exist but carry more caveats than a relational engine.
- **Vector.** Yes — Atlas Vector Search (semantic search, hybrid search) is native.
- **Agent-memory ergonomics.** Strong first-party story: the [MongoDB LangGraph Checkpointer for short-term memory and Store for long-term memory](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/), plus a [LangChain partnership](https://www.langchain.com/blog/announcing-the-langchain-mongodb-partnership-the-ai-agent-stack-that-runs-on-the-database-you-already-trust) and [long-term-memory guides](https://www.mongodb.com/company/blog/product-release-announcements/powering-long-term-memory-for-agents-langgraph). Document model maps naturally to JSON memory records.
- **SQL / AWS-native?** No SQL (MQL) / runs on AWS but is a third-party SaaS.
- **Fit.** A genuine, well-marketed competitor for the *memory* layer specifically. The trade-off vs. CockroachDB is the classic document-vs-relational one: if the agent's **operational data is relational/transactional**, MongoDB makes you either model it as documents or run a second relational system — reintroducing multi-store consistency risk. If the whole app is already document-shaped, MongoDB is compelling.

---

## 7. Amazon DynamoDB as agent memory

[DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GlobalTables.html) is AWS's serverless key-value/document store — the default "session store" in most AWS agent reference architectures (e.g., [Bedrock AgentCore + DynamoDB memory](https://medium.com/@yatharthchauhan/scale-multi-agent-ai-on-aws-bedrock-agentcore-dynamodb-memory-that-saves-94-costs-c683986a1968), [agentic AI with Bedrock + DynamoDB](https://dzone.com/articles/agentic-ai-with-bedrock-and-dynamodb-integration)).

- **Distributed & multi-region.** Yes — Global Tables. Two modes: **MREC** (multi-region eventual consistency, default, async, **last-writer-wins by timestamp**, seconds of RPO) and **MRSC** (multi-region strong consistency), which [went GA in June 2025 and provides RPO=0](https://aws.amazon.com/about-aws/whats-new/2025/06/amazon-dynamo-db-global-tables-multi-region-strong-consistency-generally-available/). MRSC is a newer, more constrained configuration than default global tables.
- **Vector.** **No native vector index.** DynamoDB is paired with a separate vector store (OpenSearch, S3 Vectors, etc.) for semantic recall — the AWS hybrid pattern uses DynamoDB for structured lookups + a vector engine for embeddings.
- **SQL / managed.** No SQL (PartiQL is limited) / fully managed serverless, deeply AWS-native.
- **Fit.** Superb for high-throughput, simple key-access session/event storage at any scale, and it's maximally AWS-native. Weaknesses for agent memory: **no native vectors** (needs a second system), **no rich relational/transactional joins** across memory + business data, and the default global-table model is eventually consistent with last-writer-wins — a real hazard when two regions update the same agent state.

---

## 8. Amazon Bedrock AgentCore Memory (the AWS-native managed agent-memory service) — the key competitor

This is the most important competitor because **the project is forced onto AWS**, and AgentCore Memory is AWS's purpose-built, first-party answer to exactly the problem statement "give my agent memory." A hackathon team could plausibly skip a database entirely and just call this API. So the honest question is: *why would anyone choose CockroachDB over it?*

**What it is.** A **fully managed** agent-memory service ([memory overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html), [AWS ML blog deep dive](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents)). AgentCore (the broader platform) was announced in preview July 2025 and reached **GA in October 2025**.

- **Short-term memory:** raw interaction events stored as **immutable, chronologically ordered events** by actor/session (via `CreateEvent`), retained for a configurable expiry up to **365 days** ([short-term memory docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/using-memory-short-term.html)).
- **Long-term memory:** **asynchronous** extraction of insights into three built-in strategies — **Semantic** (facts), **Summary** (session summaries), **User Preferences** — organized under **hierarchical namespaces** (e.g. `/customer/{actorId}/preferences/`). Retrieval is by **semantic search** and namespace queries, not SQL. A March 2026 update added [streaming notifications to Kinesis when long-term records change](https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-memory-streaming-ltm).
- **Consistency/durability.** Short-term event writes are synchronous and chronologically ordered; long-term extraction is asynchronous (eventually reflected). Encrypted at rest/in transit, KMS-supported.

**The decisive limitation — it is NOT a database.** AWS's own framing is explicit that AgentCore Memory is for *agent context retention, not operational data*: it stores "conversation history, user preferences, and agent state" — **not transactional business data**, and it exposes **no SQL and no transactions** ([AWS ML blog](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents)). It is an opinionated, event-based, semantic-memory layer — a black box that does LLM-based fact extraction for you.

### Would a team just use AgentCore Memory instead of CockroachDB?

**Reasons a team *would*:** zero infrastructure, it's the native AWS "batteries-included" path, it does the hard part (extracting durable facts/summaries/preferences from raw chat) automatically, and it's a couple of API calls. For a pure conversational chatbot with no serious operational data, it is genuinely the path of least resistance.

**What CockroachDB offers that AgentCore Memory does not:**
1. **One system of record for memory *and* operational/business data.** AgentCore explicitly is *not* for transactional data. The moment your agent must read/write real business state (orders, balances, inventory, tickets) **consistently with** its memory, you need a real transactional database anyway — and now you're running two systems that can disagree. CockroachDB collapses embeddings + relational rows + ACID transactions into one store.
2. **Strong transactional consistency & joins.** SQL, foreign keys, multi-statement ACID transactions, and joins across memory and operational tables. AgentCore has none of this — long-term memory is even eventually-consistent (async extraction).
3. **You own and can query the data directly.** It's your schema, your SQL, portable across clouds. AgentCore Memory is a proprietary AWS API with its own extraction model and namespace layout — **AWS-specific lock-in**, no SQL escape hatch, opinionated strategies you can't fully reshape.
4. **Zero-RPO / <9s-RTO region survival for the *whole* dataset** (see §10), not just an opaque managed SLA on the memory blob.
5. **Not tied to Bedrock/AWS.** If the app moves clouds or uses non-Bedrock models, CockroachDB memory travels with it.

**Honest counterpoint:** AgentCore Memory does the *semantic extraction/consolidation* (turning raw turns into durable facts, summaries, preference records) for free. With CockroachDB you store and retrieve embeddings and raw events, but **you build the extraction/summarization pipeline yourself** (or via a framework like LangGraph/LangChain, which CockroachLabs supports). So it's not purely "CockroachDB is a superset" — AgentCore trades control and single-store consistency for a managed cognitive layer. The strongest positioning is therefore **complementary/replacement by scope**: use CockroachDB when memory must be consistent with real transactional data and portable; AgentCore is tempting only when the agent is memory-only with no serious operational state.

---

## 9. Comparison table

| Store | Distributed? | Strong-consistency multi-region? | Auto-failover RPO=0? | Native vector index? | Single store (vectors + relational + txns)? | Postgres-compatible? | AWS-native? |
|---|---|---|---|---|---|---|---|
| **CockroachDB** | Yes (shared-nothing, Raft) | Yes (survival goals) | **Yes** (RPO=0, RTO<9s, automatic) | Yes (pgvector, v25.1+) | **Yes** | Yes (wire + dialect) | No (runs on AWS; not first-party) |
| PostgreSQL + pgvector | No (single-node) | No | No | Yes (pgvector) | Yes | Native | No |
| Aurora PostgreSQL + pgvector | Storage only; single writer | No (Global DB is async, RPO seconds) | Planned only; unplanned RPO seconds | Yes (pgvector 0.8) | Yes | Native | **Yes** |
| Aurora DSQL | **Yes** (active-active) | **Yes** | Yes (multi-region strong consistency) | **No pgvector** | No (no vectors, no FKs) | Subset of PG16 | **Yes** |
| Neon + pgvector | No (single writer) | No (same-region async replicas) | No | Yes (pgvector) | Yes | Native | No (SaaS on AWS) |
| YugabyteDB | **Yes** (shared-nothing, Raft) | **Yes** | Yes | Yes (USearch/HNSW via pgvector) | **Yes** | High (reuses PG layer) | No (Aeon/self-host on AWS) |
| Spanner (+ vector) | **Yes** (global) | **Yes** (TrueTime) | Yes | Yes (KNN/ANN) | **Yes** | PG dialect (not native) | No (GCP) |
| AlloyDB | Single writer + read pool | Cross-region async; switchover RPO=0 | Planned (switchover) | Yes (pgvector + ScaNN) | **Yes** | Native | No (GCP) |
| MongoDB Atlas | Yes (sharded replica sets) | Tunable (not relational strong) | Configurable | Yes (Atlas Vector Search) | Docs + vectors (no relational SQL) | No (MQL) | No (SaaS on AWS) |
| DynamoDB | **Yes** (Global Tables) | MRSC mode: **Yes** (default MREC: no) | MRSC: **Yes** (RPO=0); MREC: no | **No** (needs separate vector store) | No (KV; no vectors, no rich joins) | No | **Yes** |
| Bedrock AgentCore Memory | Managed (opaque) | Managed SLA (not a SQL guarantee) | Managed (opaque) | Semantic search (managed, not an index you own) | **No** (memory only; not operational data) | No (proprietary API) | **Yes** |

*Notes: "Auto-failover RPO=0" = automatic recovery from an unplanned failure with no data loss. Aurora PostgreSQL Global Database and DynamoDB MREC are asynchronous (seconds of RPO); DynamoDB MRSC and Aurora DSQL provide multi-region strong consistency. AgentCore's long-term memory is asynchronously extracted (eventually consistent) even though short-term events are ordered.*

---

## 10. Critical section A — CockroachDB vs. the AWS-native option (AgentCore Memory / DynamoDB / Aurora)

Since AWS is a hard constraint, the real decision is CockroachDB vs. the three AWS-native paths. Being objective:

**When the AWS-native option wins:**
- **AgentCore Memory** wins for a *memory-only* conversational agent with no serious transactional business data: it's zero-ops, first-party, and it does semantic fact/summary/preference extraction for you. If "remember the conversation" is the whole job, adding a database is over-engineering.
- **DynamoDB** wins when you need massive-scale, simple key-access session/event storage and are happy to pair it with a separate vector store; it's the cheapest, most elastic, most AWS-integrated option, and MRSC now offers RPO=0 for the tables that need it.
- **Aurora PostgreSQL** wins for a **single-region** app that wants full Postgres + mature pgvector + managed ops without leaving AWS first-party services.

**When CockroachDB is the better choice:**
- **You need memory *and* operational data in one strongly-consistent store.** This is the core thesis. AgentCore is explicitly *not* for transactional data; DynamoDB has no vectors and no relational joins; Aurora PG can't do active-active multi-region with zero-RPO region survival. CockroachDB is the only AWS-deployable option that puts **embeddings + relational rows + ACID transactions + multi-region zero-RPO** in a single system.
- **You need zero-RPO, automatic, sub-10s region survival across the whole dataset** — not just a managed SLA on an opaque memory blob. CockroachDB [guarantees RPO=0 and RTO<9s](https://www.cockroachlabs.com/docs/stable/data-resilience) with Raft consensus and [region survival goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals).
- **You want portability / no lock-in.** CockroachDB is Postgres-wire-compatible and multi-cloud; AgentCore Memory and DynamoDB are proprietary AWS APIs. If the agent might move off Bedrock or AWS, memory travels with the data.
- **You want to own the data model and query it in SQL** — join an agent's remembered facts against live business tables in one query, with real foreign keys and transactions.

**Where CockroachDB is *not* the better choice (be honest):** if the agent has no meaningful operational data and you value zero-ops + built-in memory cognition over control and portability, AgentCore Memory is the pragmatic AWS answer, and CockroachDB is more than you need. The winning pitch is not "CockroachDB replaces AgentCore for everything" — it's "**AgentCore stores an agent's memories; CockroachDB is the consistent, durable, queryable system of record for agents that also touch real transactional data — the thing AgentCore/DynamoDB explicitly are not.**"

---

## 11. Critical section B — CockroachDB vs. Spanner / YugabyteDB (its closest architectural peers)

These three are the same species: shared-nothing (or globally-distributed), strongly-consistent, horizontally-scalable SQL with native vectors and one-store consistency. Differences are about ecosystem and deployment, not category.

- **vs. Google Spanner.** Spanner is the pioneer of globally-consistent SQL (TrueTime) and has native [KNN/ANN vector search](https://docs.cloud.google.com/spanner/docs/find-k-nearest-neighbors). But it's **GCP-only** (disqualifying under the AWS constraint), uses GoogleSQL (Postgres only as a limited dialect), and is proprietary. CockroachDB's edge: **runs on AWS, Postgres-wire compatibility**, open-source core, multi-cloud portability.
- **vs. YugabyteDB.** The closest true rival — open-source, Postgres-compatible, shared-nothing, Raft, with [distributed HNSW vector indexing (USearch)](https://www.yugabyte.com/blog/yugabytedb-vector-indexing-architecture/) tested to [100M+ vectors](https://www.yugabyte.com/blog/agentic-ai-and-extensible-vector-search/). Value proposition is nearly identical to CockroachDB. Differentiators come down to specifics rather than category: CockroachDB's declarative multi-region abstractions (`REGIONAL BY ROW`, `GLOBAL` tables, survival goals) and its serverless/managed Cloud, maturity of the vector implementation, transaction/contention behavior, and ecosystem integrations (LangChain/LangGraph, managed MCP server). Both reuse or emulate the Postgres surface; Yugabyte reuses the actual PG query layer, CockroachDB reimplements it. For a hackathon, the honest line is that Yugabyte is a legitimate architectural equal, and CockroachDB's advantages are in **multi-region ergonomics, managed-cloud polish, and AI-stack integrations** rather than a categorical capability Yugabyte lacks.

**Bottom line for peers:** CockroachDB's defensible position against Spanner is "same guarantees, but AWS-deployable, Postgres-compatible, and not locked to one cloud." Against Yugabyte it's a narrower, execution-level argument (multi-region UX, managed offering, vector/AI ecosystem maturity), not a categorical one.

---

## Sources
- CockroachDB: [Vector search with pgvector](https://www.cockroachlabs.com/blog/vector-search-pgvector-cockroachdb/), [Agentic AI architecture for memory & control](https://www.cockroachlabs.com/blog/agentic-ai-architecture-memory-control/), [Data resilience (RPO=0/RTO<9s)](https://www.cockroachlabs.com/docs/stable/data-resilience), [Multi-region survival goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals), [RTO vs RPO](https://www.cockroachlabs.com/glossary/distributed-db/rto-vs-rpo/)
- Aurora DSQL: [GA announcement](https://aws.amazon.com/about-aws/whats-new/2025/05/amazon-aurora-dsql-generally-available), [paper](https://arxiv.org/abs/2607.13276), [design guide](https://hidekazu-konishi.com/entry/amazon_aurora_dsql_design_decision_guide.html), [limits/limitations](https://andrewbaker.ninja/2025/11/19/amazon-aurora-dsql-performance-limits-architecture/), [vs YugabyteDB](https://www.yugabyte.com/blog/aurora-dsql-compared-to-yugabytedb/)
- Aurora PostgreSQL + pgvector: [pgvector 0.8.0 on Aurora](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/), [running pgvector in production](https://aws.amazon.com/blogs/database/running-pgvector-in-production-on-amazon-aurora-postgresql/), [Global Database DR](https://aws.amazon.com/blogs/database/cross-region-disaster-recovery-using-amazon-aurora-global-database-for-amazon-aurora-postgresql/)
- Neon: [Read replicas](https://neon.com/docs/introduction/read-replicas), [same-region replicas](https://neon.tech/blog/introducing-same-region-read-replicas-to-serverless-postgres), [as a vector DB](https://cookbook.openai.com/examples/vector_databases/neon/readme)
- YugabyteDB: [Vector indexing architecture](https://www.yugabyte.com/blog/yugabytedb-vector-indexing-architecture/), [agentic AI + extensible vector search](https://www.yugabyte.com/blog/agentic-ai-and-extensible-vector-search/), [multi-agent AI](https://www.yugabyte.com/blog/multi-agent-ai-with-yugabytedb-vector/)
- Spanner/AlloyDB: [Spanner KNN](https://docs.cloud.google.com/spanner/docs/find-k-nearest-neighbors), [ScaNN vs pgvector HNSW](https://cloud.google.com/blog/products/databases/how-scann-for-alloydb-vector-search-compares-to-pgvector-hnsw), [AlloyDB cross-region replication](https://docs.cloud.google.com/alloydb/docs/cross-region-replication/about-cross-region-replication)
- MongoDB Atlas: [LangGraph integration](https://www.mongodb.com/docs/atlas/ai-integrations/langgraph/), [LangChain partnership](https://www.langchain.com/blog/announcing-the-langchain-mongodb-partnership-the-ai-agent-stack-that-runs-on-the-database-you-already-trust), [long-term memory for agents](https://www.mongodb.com/company/blog/product-release-announcements/powering-long-term-memory-for-agents-langgraph)
- DynamoDB: [Global tables](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GlobalTables.html), [MRSC GA (RPO=0)](https://aws.amazon.com/about-aws/whats-new/2025/06/amazon-dynamo-db-global-tables-multi-region-strong-consistency-generally-available/), [Bedrock + DynamoDB agent memory](https://dzone.com/articles/agentic-ai-with-bedrock-and-dynamodb-integration)
- Bedrock AgentCore Memory: [Memory overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html), [short-term memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/using-memory-short-term.html), [memory types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-types.html), [ML blog deep dive](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents), [streaming notifications](https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-memory-streaming-ltm)
