# 01 — Vector Databases & Vector-Search Options (competitive reference)

**Purpose:** an objective, sourced map of dedicated vector databases and vector-search options, so
the team knows where **CockroachDB as agentic memory** genuinely wins and where it doesn't. The lens
throughout is **long-term AI-agent memory**: constant small writes, heavy metadata filtering, the need
for read-your-writes, transactional consistency between *memory* and *application/source-of-truth data*,
and multi-region durability.

> **The central axis — the "consistency gap."** If an agent's embeddings live in a store *separate*
> from its source-of-truth data (or in an *index* that updates asynchronously from the data), then a
> memory write and an app-state write are two independent operations that can **drift out of sync** on
> partial failure, and a just-written memory may **not be retrievable** on the next query. Most systems
> below are either standalone vector stores or use an eventually-consistent index. The few that co-locate
> vectors with data in one ACID transaction (pgvector, and CockroachDB) close that gap — but pgvector
> only does so on a single node. That combination — **single-store + SQL + ACID + horizontal scale +
> multi-region active-active** — is the CockroachDB thesis.

Sources are cited inline as markdown links. Dollar figures from third-party pricing blogs are directional;
treat vendor docs as authoritative. Research current as of mid-2026.

---

## 1. Pinecone

**Architecture & index types.** Pinecone's GA product is **Serverless** (on AWS since early 2024): storage
is fully decoupled from compute — vectors live in **S3**, a stateless pool of executors serves queries
([how-pinecone-works](https://www.pinecone.io/how-pinecone-works/),
[serverless-architecture](https://www.pinecone.io/blog/serverless-architecture/)). Data is organized into
immutable **"slabs,"** and Pinecone **auto-selects a proprietary index per slab** rather than exposing
HNSW/IVF as a knob: *Ananas* (SimHash/JL transform) for tiny slabs, *PQFS* (product quantization) for
medium, **IVF** for large (>100K) ([slab-architecture](https://www.pinecone.io/learn/slab-architecture/),
[how-pinecone-works](https://www.pinecone.io/how-pinecone-works/)). "You never choose or tune an
algorithm." **Scale:** "millions to billions" of vectors, any dimension at full precision; metadata capped
at **40 KB/record**, `$in`/`$nin` ≤ 10,000 values ([limits](https://docs.pinecone.io/reference/quotas-and-limits)).

**Consistency & durability.** **Eventually consistent by default** — "there can be a slight delay before
new or changed records are visible to queries"
([data-freshness](https://docs.pinecone.io/guides/index-data/check-data-freshness)). **No multi-record ACID
transactions.** Freshness is *verifiable but not guaranteed*: each write gets a monotonic **LSN** you can
compare against a query-response header to confirm your write landed. Durability is strong — writes hit a
**WAL on S3**, acked <100 ms once durable, then flushed to immutable slabs
([how-pinecone-works](https://www.pinecone.io/how-pinecone-works/)).

**Multi-region / HA.** Serverless indexes are **pinned to one region, immutable after creation**
([create-an-index](https://docs.pinecone.io/guides/index-data/create-an-index)). Within a region,
deployments span multiple AZs. **No built-in cross-region/active-active replication, no published RPO/RTO**
— multi-region is a DIY app-layer job. (A "global API" exists but is control-plane, not data replication,
[global-api](https://www.pinecone.io/blog/global-api/).)

**Standalone or source-of-truth?** **Pure standalone vector store** — vectors + ≤40 KB JSON metadata only.
Embeddings live entirely separately from your primary DB → textbook consistency-gap/drift risk.

**Pricing.** Usage-based on **read units / write units / storage / egress**: storage ~$0.33/GB-mo, writes
~$2/1M WU, reads ~$8.25/1M RU ([read-units](https://www.pinecone.io/learn/read-units/),
[understanding-cost](https://docs.pinecone.io/guides/manage-cost/understanding-cost)). Free Starter tier
(2 GB, AWS us-east-1 only).

**For agent memory.** *Pros:* zero-ops serverless, low write-ack latency for high-frequency memory appends,
namespaces for per-agent isolation, LSN lets you *verify* a write before reading. *Cons:* **no
transactional tie between memory and app data**; **eventual consistency** (poll the LSN or risk missing a
just-written memory); **single-region only**, no native cross-region durability; 40 KB metadata cap limits
co-located context.

---

## 2. Weaviate

**Architecture & index types.** Open-source **object + vector database** — each object is a full JSON
document stored alongside its vector(s). Four index types: **HNSW** (default, in-memory graph), **Flat**
(disk brute-force for tiny per-tenant collections), **Dynamic** (flat→HNSW auto-upgrade), and **HFresh**
(memory-efficient cluster index)
([vector-index docs](https://docs.weaviate.io/weaviate/config-refs/indexing/vector-index)). Sharded,
horizontally scalable, with strong **native multi-tenancy** (per-tenant indexes, activate/offload) —
excellent for per-user/per-agent isolation
([multi-tenancy](https://weaviate.io/blog/weaviate-multi-tenancy-architecture-explained)). HNSW is
RAM-heavy, so scale is bounded by cluster memory.

**Consistency & durability.** Splits the two planes:
**metadata/schema uses Raft** (strongly consistent, quorum commit); **object/vector data is leaderless and
eventually consistent (BASE)**
([replication-architecture](https://docs.weaviate.io/weaviate/concepts/replication-architecture),
[consistency](https://docs.weaviate.io/weaviate/concepts/replication-architecture/consistency)).
**Tunable per-op consistency** ONE / QUORUM (default) / ALL — `r + w > n` gives effectively strong reads at
a latency cost. **No ACID / no multi-object transactions.** Repair via Merkle-tree async replication and
repair-on-read; durability via per-shard WAL + backups.

**Multi-region / HA.** **Multi-datacenter within one cluster since v1.31** — nodes across regions, data
survives a full DC outage; failover via redundancy + leaderless reads
([multi-dc](https://docs.weaviate.io/weaviate/concepts/replication-architecture/multi-dc)). Sharp edges:
cross-DC latency tuning, **gossip unencrypted by default** (needs VPN). **No published RPO/RTO** — only
uptime SLAs (Flex 99.5%, Plus/Premium 99.9%, [pricing](https://weaviate.io/pricing)).

**Standalone or source-of-truth?** **More than an index — an object database.** It stores the source JSON
*with* the vector, so a memory item and its embedding never drift from *each other*. But it's **not a
transactional system of record** for your broader app (no ACID, no cross-entity transactions), so
consistency between Weaviate and an *external* operational DB is still a non-transactional app concern.

**Pricing.** Repriced Oct 2025; billed on **stored vector dimensions × replication factor**, object
storage, backups ([pricing update](https://weaviate.io/blog/weaviate-cloud-pricing-update)). Sandbox (free
14-day), Flex (~$45/mo), Plus (~$280/mo), Premium/BYOC (custom). **Replicas multiply billed dimensions** —
HA/multi-region is not free. OSS self-host available.

**For agent memory.** *Pros:* object+vector stored together (no internal drift), strong hybrid (BM25+vector)
filtering over rich JSON, best-in-class multi-tenancy, tunable up to strong reads, real multi-region
redundancy, no lock-in. *Cons:* **no ACID across systems** (can't atomically tie memory to app-DB writes),
eventually-consistent default, RAM-intensive at scale, cost scales with replication factor, no stated
RPO/RTO.

---

## 3. Qdrant

**Architecture & index types.** Rust engine; core index is a **custom HNSW** (tunable `m`, `ef_construct`,
`hnsw_ef`) ([HNSW](https://qdrant.tech/course/essentials/day-2/what-is-hnsw/)). **Quantization** — scalar
(~75% cut), binary, product — up to ~40× memory reduction at billion-scale
([resource optimization](https://qdrant.tech/articles/vector-search-resource-optimization/)). **No GPU
indexes** (CPU/Rust-optimized). Sharded, horizontally scalable across nodes; data in per-node **segments**;
reaches billions of vectors via quantization + sharding
([distributed study](https://arxiv.org/html/2509.12384v1)).

**Consistency & durability.** Writes hit a per-shard **WAL**, then an update queue applies them
([data updates](https://deepwiki.com/qdrant/qdrant/6-data-updates-and-consistency)). Claims **immediate
searchability** — runs full-scan over not-yet-indexed small segments while HNSW builds in the background, so
**no read-visibility gap for the writing client** even though indexing lags
([FAQ](https://qdrant.tech/documentation/faq/qdrant-fundamentals/)). Replication is per-op configurable
(`write_consistency_factor`, write ordering weak/medium/strong) but **there are no ACID/multi-record
transactions** ([write consistency](https://deepwiki.com/qdrant/qdrant/6.3-write-consistency-and-replication)).

**Multi-region / HA.** HA + backup/DR bundled into paid Cloud tiers (Premium 99.9% SLA), but HA is
fundamentally **replica-based within a region** — **no stated cross-region RPO/RTO**; cross-region is a
backup/DIY story ([pricing](https://qdrant.tech/pricing/)).

**Standalone or source-of-truth?** **Explicitly a dedicated vector-search layer, not a system of record** —
Qdrant even ships a "sync with Postgres" guide, confirming primary data lives elsewhere
([sync with Postgres](https://qdrant.tech/documentation/data-synchronization/with-postgres/)). Points are
vector + JSON payload → embeddings separate from primary data → drift risk.

**Pricing.** Free tier (0.5 vCPU/1 GB, permanent); Managed Cloud usage-based on vCPU/RAM/storage
(~$0.078/GB-hr); Hybrid Cloud (your infra) and Private Cloud on-prem
([pricing](https://qdrant.tech/pricing/)).

**For agent memory.** *Pros:* excellent Rust latency, **best-in-class payload/metadata filtering**,
immediate read-your-writes (good for constant small writes), simple ops. *Cons:* **no ACID/transactions**
(memory and app data can't commit atomically), weaker multi-region durability (no published RPO/RTO), no
GPU/disk-native billion-scale index; positioned as a **satellite store**, not source of truth.

---

## 4. Milvus (OSS) / Zilliz Cloud (managed)

**Architecture & index types.** Cloud-native, **decoupled compute/storage**; widest index matrix —
**FLAT, IVF, HNSW, SCANN, DiskANN**, plus **GPU indexes incl. NVIDIA CAGRA**
([overview](https://milvus.io/docs/overview.md), [GPU index](https://milvus.io/docs/gpu_index.md)). CAGRA
builds 10–50× faster than CPU HNSW with sub-ms p50 but must fit VRAM (~50M vectors/GPU)
([CAGRA blog](https://milvus.io/blog/faster-index-builds-and-scalable-queries-with-gpu-cagra-in-milvus.md));
**DiskANN** (NVMe) is the practical **billion-scale** path. Cited at **tens of billions of vectors** in
production. Deploy as Lite (embedded) / Standalone / Distributed (K8s). **Strongest scale + GPU story here.**

**Consistency & durability.** Uniquely exposes **four tunable consistency levels** via a `GuaranteeTs`
mechanism ([consistency](https://milvus.io/docs/consistency.md)): **Strong** (wait for full visibility),
**Bounded = default** (bounded staleness — best latency/recall), **Session** (client always reads its own
writes), **Eventually** (fastest, no ordering). ⚠️ **Under the default (Bounded), a freshly inserted vector
is NOT immediately queryable** unless you raise to Session/Strong (latency cost). **No ACID multi-row
transactions.** Durability is cloud-native: **object storage** (S3/MinIO) + **message queue/WAL**
(Pulsar/Kafka/Woodpecker) + **etcd** metadata.

**Multi-region / HA (Zilliz).** Genuine strength. Dedicated clusters run **multi-AZ, up to 99.95% SLA**;
**Global Cluster** does CDC cross-region/cross-cloud replication with **published DR numbers**: planned
switchover **RPO = 0, RTO < 30 s**; auto-failover **RPO ≈ CDC lag (seconds), RTO < 60 s**
([Global Cluster](https://zilliz.com/blog/zilliz-global-cluster),
[data resilience](https://docs.zilliz.com/docs/data-resilience)).

**Standalone or source-of-truth?** **Specialized vector DB, not a transactional source of truth**
([overview](https://milvus.io/docs/overview.md)). Embeddings live apart from operational data → same drift
risk as Qdrant.

**Pricing.** Milvus OSS free (Apache 2.0, but you run etcd + object store + message queue). Zilliz Cloud:
Free / Serverless (~$0.35–$4 per M vCU) / Dedicated (~$99/mo+, multi-AZ) / BYOC; storage standardizing to
$0.04/GB-mo Jan 2026 ([pricing](https://zilliz.com/pricing)).

**For agent memory.** *Pros:* **best scale ceiling** (tens of billions), GPU/DiskANN indexing, **tunable
consistency** (use Session so an agent reads its own writes; Strong when needed), **strongest multi-region
DR** (RPO=0/RTO<30 s). *Cons:* **default is stale-by-design** (gotcha for constant-write agent loops), **no
ACID** between memory and app data, self-hosted Milvus is **operationally heavy**; easy path is Zilliz lock-in.

---

## 5. Redis Vector Search (Redis Query Engine / RediSearch, Redis 8)

**Architecture & index types.** Vector search via the **Redis Query Engine** over hashes/JSON, **in-memory**.
Three indexes ([vector concepts](https://redis.io/docs/latest/develop/ai/search-and-query/vectors/)):
**FLAT** (brute force, <1M), **HNSW** (default >1M), **SVS-VAMANA** (Intel compression index, Redis 8.2).
Redis 8 adds **INT8/UINT8 quantized vectors** ([vector indexes](https://redis.io/blog/vector-indexes-in-redis/)).
**Scale ceiling = RAM** — the entire index and dataset must fit in memory (the most expensive resource to
scale); shardable but you still pay RAM for every vector.

**Consistency & durability.** Redis's **weakest axis**. In-memory first, **not durable by default**: **AOF
disabled by default**, RDB snapshots can "lose the latest minutes of data" on unclean stop, AOF `everysec`
can lose ~1 s ([persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/),
[durability](https://redis.io/tutorials/operate/redis-at-scale/persistence-and-durability/)). **Replication
is async** — a primary crash before propagation loses acked writes; synchronous requires the `WAIT` command
(added latency, not default). **No SQL-style multi-key ACID.**

**Multi-region / HA.** Single-cluster primary/replica auto-failover (async → loss window). **Redis
Enterprise Active-Active** uses **CRDTs** for true writable multi-master per region — a genuine strength
([active-active](https://redis.io/active-active/)). But CRDTs resolve by data-type rules (LWW/counters),
**not serializable consistency**, and cross-region propagation is **async** (transient divergence). It's a
commercial feature, not OSS.

**Standalone or source-of-truth?** General-purpose store, but **in practice a cache/ephemeral tier**, not
source of truth. Key-value/document model, no relational joins, no cross-entity ACID → you generally
**cannot** commit a memory write and the app's transactional business data atomically → consistency gap
persists.

**Pricing.** OSS free (note the 2024 RSALv2/SSPLv1 license change). Redis Cloud is **RAM-based** (~$0.881/GB-mo
+ requests, Pro from ~$200/mo); crucially **HA doubles billed memory, Active-Active multiplies per region**
([pricing overview](https://upstash.com/blog/redis-pricing-comparison-every-major-provider-in-2026-with-numbers)).

**For agent memory.** *Pros:* lowest-latency hot memory tier, handles constant writes, rich hybrid filtering
(TAG/NUMERIC/GEO + KNN in one `FT.SEARCH`), real active-active via CRDTs. *Cons:* **not durable by default**,
async-replication **loss window on failover** (bad for memory you must never lose), **RAM-bound cost**,
**no relational ACID** with app data. Best fit: **hot working-set memory / semantic cache**, not the durable
transactionally-consistent long-term store.

---

## 6. pgvector on vanilla (single-node) PostgreSQL

**Architecture & index types.** Postgres extension adding a native `vector` type (up to **16,000 dims**;
64,000 binary) stored in **ordinary Postgres tables** ([pgvector](https://github.com/pgvector/pgvector)).
**IVFFlat** (fast build, lower recall) and **HNSW** (better queries, RAM-heavy build). **StreamingDiskANN**
via the separate **[pgvectorscale](https://github.com/timescale/pgvectorscale)** extension is **disk-based**
— bounded RAM regardless of dataset size — with Statistical Binary Quantization; Tiger Data benchmarks claim
**28× lower p95 latency / 16× higher throughput vs Pinecone s1 at 99% recall, 75% cheaper self-hosted** on
50M embeddings. pgvector 0.8.0 improved filtered-query recall via iterative scans
([0.8.0 release](https://www.postgresql.org/about/news/pgvector-080-released-2952/)). **Scale ceiling =
single-node Postgres** — all writes funnel through one primary; you scale *vertically* + read replicas.
**No horizontal write scale.**

**Consistency & durability.** **pgvector's core strength.** Vectors are first-class rows, inheriting **full
Postgres ACID, WAL durability, PITR**. A committed insert is durable and **immediately queryable in the same
session — no eventual-consistency window** — and participates in normal multi-statement transactions with
real isolation and constraints. Correct semantics for memory that must not be lost and must be read-your-writes.

**Multi-region / HA.** Vanilla Postgres's **weak axis**: HA is **single-primary streaming replication** +
read replicas + failover, with a **data-loss window** on async failover (or commit-latency cost for sync).
**No native multi-region active-active / multi-master** — remote regions are read-only replicas.

**Standalone or source-of-truth? — the key strength.** Vectors live **inside your existing Postgres**, so
memory embeddings sit in the **same DB, same schema, same transaction** as relational source-of-truth data.
You can `INSERT` the app row + its embedding + metadata and `COMMIT` them **atomically** — **no dual-write,
no cross-store sync, no consistency gap.** Metadata filtering is plain SQL `WHERE` + joins. **The honest
limit: you get this only at single-node scale** — one ACID store, but not horizontally scalable writes and
not multi-region active-active.

**Pricing.** pgvector is **free** (no per-vector/query fee); you pay only for the Postgres instance's
compute+storage. pgvectorscale also free/OSS. Storage-based → cheaper than Redis for large corpora, esp.
with DiskANN. Supported on RDS and **Aurora PostgreSQL (0.8.0)**
([Aurora announcement](https://aws.amazon.com/about-aws/whats-new/2025/04/pgvector-0-8-0-aurora-postgresql)).

**For agent memory.** *Pros:* true ACID/WAL durability, immediate read-your-writes, **memory + app data in
one transaction / one store (no gap)**, rich SQL filtering + joins, DiskANN bounded-memory scale, free.
*Cons:* **single-node** — no horizontal write scale, capacity capped by one primary, failover loss window,
**no native multi-region active-active**.

> **CockroachDB-relevant framing.** pgvector is the **same engine class as CockroachDB — Postgres-compatible,
> single-store, SQL, ACID** — and delivers the attractive part of the story (vectors next to source-of-truth
> data, committed atomically) **but only on one node**. What it structurally *cannot* give is **horizontal
> write scale + multi-region active-active durability**. Redis has active-active but sacrifices relational
> ACID + transactional colocation; vanilla Postgres keeps ACID/single-store but sacrifices scale-out and
> active-active. **CockroachDB is positioned to offer both at once.**

---

## 7. Managed option — MongoDB Atlas Vector Search (+ AWS OpenSearch)

### MongoDB Atlas Vector Search

**Architecture & index types.** Built on **Apache Lucene**, using **HNSW** (plus exact-NN mode). The
defining fact: vector indexes do **not** live in the core `mongod` — they're built/served by a **separate
`mongot` process on dedicated Search Nodes**, so vector load scales independently of OLTP
([overview](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/)). Vectors ≤ **8192
dims**; **quantization** — scalar int8 (~3.75× RAM cut) and binary int1 (32× compression, ~24× RAM cut, with
auto-rescoring) ([quantization](https://www.mongodb.com/docs/vector-search/about/vector-quantization/)).

**Consistency & durability — the key nuance.** **Data and index have different guarantees.** The **document
data** is strongly consistent: document-level and **multi-document ACID transactions**, **majority write
concern** durable (survives failover, RPO=0)
([HA](https://www.mongodb.com/docs/atlas/architecture/current/high-availability/)). But the **vector index
is eventually consistent** — `mongot` tails the **change stream/oplog** and updates Lucene on separate
hardware across a network hop: "Atlas Search is eventually consistent, and changes… will, eventually, be
reflected" ([when NOT to use Atlas Search](https://medium.com/mongodb/when-not-to-use-atlas-search-5697341ad61f)).
So **a freshly written document is not immediately visible in vector results**; under heavy writes the lag
grows and worst-case triggers a full rebuild serving **stale results**
([alert resolutions](https://www.mongodb.com/docs/atlas/reference/alert-resolutions/atlas-search-alerts/)).
Honest framing: **data is a single logical store** (a real advantage — no separate vector DB to sync), **but
the index over it is eventually consistent**, not transactionally in sync.

**Multi-region / HA.** Every cluster is a **≥3-node replica set across AZs**; **automatic failover in
seconds**, majority writes → **RPO=0, RTO seconds**; **multi-region/multi-cloud** replication (5 nodes/3
regions for regional survival); continuous backups PITR (~1-min RPO); Global Clusters (zone sharding)
([multi-region](https://www.mongodb.com/docs/atlas/architecture/current/deployment-paradigms/multi-region/)).
Caveat: these apply to the operational replica set; the **Search index still catches up asynchronously**
after failover.

**Standalone or source-of-truth?** **Both — MongoDB's edge vs Pinecone.** Operational documents + metadata +
vectors live in **one logical store**; metadata filter + vector search in one query, no ETL
([unified vs split](https://www.mongodb.com/company/blog/technical/strategic-database-architecture-for-ai-unified-vs-split)).
Limits: it's a **document DB, not relational/SQL, not strongly-consistent distributed SQL**, and "unified"
holds for *storage*, not *index freshness*.

**Pricing.** Cluster-tier billing (M10+ ~$0.08/hr+) **plus Search Nodes billed separately per node-hour**
(2-node HA minimum, higher-memory instances for vectors) — additive cost, not "free vector search on the DB
you already have" ([cluster costs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/)).

**For agent memory.** *Pros:* **unified memory + app data** (no external sync), memory *records* get ACID +
majority-durable writes (RPO=0), independent Search-Node scaling, quantization, mature multi-region HA/PITR.
*Cons:* **indexing lag / eventual consistency is the big one** — write a memory, immediately query it, and it
**may not be found yet**; **no read-your-writes on vector retrieval**; consistency is **split** (transactions
cover data, not index); not relational/distributed-SQL; additive always-on Search-Node cost.

### AWS OpenSearch Service (alternative managed option)

Vector search via the **k-NN plugin** (`knn_vector`, ≤10,000 dims), backends **Lucene** (HNSW+filtering),
**Faiss** (HNSW/IVF + PQ/fp16), legacy **NMSLIB** (deprecated 2.19, blocked in 3.0)
([k-NN docs](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/knn.html),
[methods & engines](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/)).
Managed, multi-AZ with replica shards; vector graphs in node memory (drives sizing). Architecturally a
**standalone search index separate from your source-of-truth DB** — you own the ingestion pipeline, so it's
**eventually consistent by construction** with no transactional link back to the operational DB. Battle-tested
and a natural AWS-stack fit (e.g., Bedrock Knowledge Bases), but for agent memory it shares the bolt-on-index
weakness: **retrieval freshness lags writes, and keeping the memory store and index consistent is your job,
not the database's.**

---

## Comparison table

| System | Transactional (ACID)? | Strong consistency (read-your-writes on vector query)? | Multi-region auto-failover? | Single-store (vectors + source-of-truth data together)? | SQL? | Managed offering? |
|---|---|---|---|---|---|---|
| **Pinecone** | No | No (eventual; verify via LSN) | No (single-region, no RPO/RTO) | No (standalone; vectors + 40KB metadata) | No | Yes (serverless) |
| **Weaviate** | No | Tunable (QUORUM default; ALL for strong) | Multi-DC redundancy, no stated RPO/RTO | Partial (object+vector together, not app source-of-truth) | No (GraphQL/REST) | Yes (Cloud + OSS) |
| **Qdrant** | No | Yes for writer (immediate searchability) | Within-region replicas, no cross-region RPO/RTO | No (dedicated layer; "sync with Postgres") | No | Yes (Cloud + OSS) |
| **Milvus / Zilliz** | No | Tunable (Bounded default = stale; Session/Strong opt-in) | Yes — Global Cluster RPO=0 / RTO<30s (planned) | No (specialized vector DB) | No | Yes (Zilliz + OSS) |
| **Redis** | No (no multi-key ACID) | Not durable by default; async repl | Active-Active CRDT (async, not serializable) | Possible but usually a cache; no relational ACID | No | Yes (Redis Cloud + OSS) |
| **pgvector (vanilla PG)** | **Yes (full Postgres ACID)** | **Yes (immediate, same txn)** | **No** (single primary + read replicas) | **Yes** (same DB/txn as relational data) — single-node only | **Yes** | Yes (RDS/Aurora) |
| **MongoDB Atlas** | Data: yes (multi-doc ACID). Index: no | No — index eventually consistent (mongot lag) | Yes (RPO=0/RTO seconds, data plane) | Partial (one store, but eventually-consistent index) | No | Yes (Atlas) |
| **AWS OpenSearch** | No | No (eventual, you own ingestion) | Multi-AZ; cross-region is DIY | No (standalone index) | No | Yes (AWS) |
| **→ CockroachDB (C-SPANN)** | **Yes (distributed serializable ACID)** | **Yes (vector write ACID + immediately queryable in same txn)** | **Yes (multi-region survivability, auto-failover, RPO≈0)** | **Yes (vectors + relational data, one store, one txn)** | **Yes (Postgres-compatible)** | **Yes (CockroachDB Cloud)** |

---

## Where CockroachDB wins vs. these — and where it doesn't

CockroachDB's vector story is **C-SPANN** (CockroachDB SPANN), a **disk-based, distributed** ANN index based
on Microsoft's **SPANN/SPFresh** and Google's **ScaNN**, using a hierarchical K-means tree that stays shallow
at scale (10B vectors ≈ 5 levels) with **RaBitQ quantization** (~94% index-size reduction). It handles live
inserts/deletes with background splits/merges and ships **pgvector-compatible** syntax (`<->`, `<=>`, `<#>`),
GA-track from **v25.2+**
([distributed vector indexing](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/),
[C-SPANN: real-time indexing for billions](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/),
[VECTOR type docs](https://www.cockroachlabs.com/docs/v26.2/vector),
[how CockroachDB built vector indexing](https://blog.bytebytego.com/p/how-cockroachdb-built-vector-indexing)).

**Where CockroachDB wins (the load-bearing differentiators for agent memory):**

- **Single-store, no consistency gap.** Vectors live **in the same distributed SQL database, in the same ACID
  transaction** as the agent's structured/operational data. You `INSERT` a memory + its embedding + metadata
  + any app-state change and `COMMIT` atomically. Every standalone vector DB here (Pinecone, Qdrant, Milvus,
  Weaviate, OpenSearch) forces embeddings into a **separate store** you must sync — the drift risk the whole
  category has. MongoDB unifies *storage* but its **index is eventually consistent**; only pgvector and
  CockroachDB truly close the gap.
- **Strong consistency + read-your-writes on the vector index.** A committed vector is **immediately
  queryable** — no LSN polling (Pinecone), no `mongot`/change-stream lag (MongoDB/OpenSearch), no
  stale-by-default window (Milvus Bounded). This directly fixes the classic agent failure: *"I just wrote a
  memory and can't retrieve it."*
- **Multi-region durability with SQL semantics.** Distributed serializable ACID + multi-region survivability
  and auto-failover in **one system**. pgvector can't do multi-region active-active at all; Redis active-active
  gives up serializable consistency and relational ACID; Pinecone/Qdrant publish no cross-region RPO/RTO.
  (Milvus/Zilliz and MongoDB have strong multi-region DR, but **without** the single-txn ACID tie between
  memory and app data.)
- **SQL + relational modeling.** Joins, constraints, and metadata filtering are first-class SQL over the same
  rows as the vectors. Only pgvector matches this — and only on a single node.
- **It's the union pgvector and Redis each only half-deliver.** Postgres-compatible single-store ACID **and**
  horizontal scale **and** multi-region active-active, at once. That specific union is unique to CockroachDB
  in this set.

**Where CockroachDB does NOT win (be honest):**

- **Raw ANN throughput / recall / index maturity.** Purpose-built engines (Qdrant's Rust HNSW, Milvus with
  GPU **CAGRA** and DiskANN, Pinecone's tuned proprietary indexes) are optimized for one job and will
  generally show **higher raw QPS, lower p99, and more index/algorithm choices** than a general-purpose SQL
  engine. C-SPANN is newer (v25.2+ GA-track) and has published **limitations** (merges/reassignments not
  fully implemented, some ALTER/IMPORT gaps, expanding distance-metric and SIMD support)
  ([C-SPANN blog](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/)).
- **Index-type breadth & GPU.** No GPU indexing (vs Milvus CAGRA), no user choice among HNSW/IVF/DiskANN
  variants — C-SPANN is the one algorithm.
- **Extreme scale ceiling & specialized tuning.** Milvus/Zilliz demonstrably run **tens of billions** of
  vectors with fine-grained index/quantization tuning; that ecosystem is more mature for pure vector scale.
- **Simplicity for a pure vector cache.** If you only need a hot, ephemeral semantic cache with no
  transactional tie to app data, Redis or a serverless Pinecone index is simpler and cheaper than standing up
  distributed SQL.
- **Latency for hot in-memory reads.** Redis's in-memory path will beat a disk-based distributed index on raw
  single-lookup latency for a resident working set.

**Net positioning.** CockroachDB doesn't win the "fastest raw ANN" or "most index knobs" contest — dedicated
vector DBs do. It wins the contest that actually defines **long-term agent memory**: memory that is
**durable, strongly consistent, transactionally married to application data, queryable the instant it's
written, and survivable across regions — in a single SQL store.** The competitive wedge is the **consistency
gap**: everyone else either separates vectors from source-of-truth data or updates the vector index
asynchronously; CockroachDB and (single-node) pgvector are the only ones that don't — and only CockroachDB
adds horizontal scale + multi-region active-active on top.
