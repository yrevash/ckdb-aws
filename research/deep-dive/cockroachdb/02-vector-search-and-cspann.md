# 02 — Vector Search and the C-SPANN Index

A deep reference on CockroachDB's native `VECTOR` type and its distributed ANN index, **C-SPANN**,
for the "agentic memory" hackathon build. Verified directly against the CockroachDB docs source
(`cockroachdb/docs` GitHub repo, versions v25.2 → v26.2) and official blog posts, July 2026.

---

## 1. Timeline: from pgvector-compatible column to distributed C-SPANN index

| Version | Date (approx.) | What shipped | Status |
|---|---|---|---|
| **v24.2** | mid‑2024 | `VECTOR` data type + pgvector-compatible operators (`<->`, `<=>`, `<#>`). Brute-force (sequential-scan) similarity search only — **no index**. | Preview |
| **v25.2** | 2025‑06‑04 ("10-year release") | **C-SPANN** vector index ships: `CREATE VECTOR INDEX`, RaBitQ quantization, prefix-column partitioning. Only **L2 distance (`<->`)** accelerated at launch. | Preview (`feature.vector_index.enabled` flag) |
| **v25.3** | ~Q3 2025 | Vector indexes gain **cosine distance** (`vector_cosine_ops`) and **inner product** (`vector_ip_ops`) opclasses, matching pgvector's three metrics. | Still preview |
| **v25.4** | ~Q4 2025 / early 2026 | Preview banner/callout removed from the docs source for `vector-indexes.md` — indexing moves out of the explicit "preview" feature-phase. | **No longer flagged as preview** (see caveat below) |
| **v26.1 / v26.2** | 2026‑02, 2026‑04 | Same page, same absence of a preview banner; docs continue to refine tuning guidance, known-limitations list shrinks (the "multiple column families give wrong results" and "backfill blocks all mutations" bugs from the v25.2 known-limitations list are gone from the current list). | Current stable (v26.2, GA'd 2026‑04‑27) |

**Honest caveat on GA status:** I could not find a dedicated Cockroach Labs blog post or release-notes line item that says "vector indexes are now Generally Available" in so many words. What I *can* verify directly from the docs source (`raw.githubusercontent.com/cockroachdb/docs`) is that the `{% include_cached feature-phases/preview.md %}` callout — which is present verbatim on the v25.2 and v25.3 `vector-indexes.md` pages — **is absent** from v25.4, v26.1, and v26.2. Cockroach Labs' docs convention is that the preview/GA feature-phase banner is the canonical status marker, so its removal is strong evidence of GA as of v25.4. Still, `feature.vector_index.enabled` remains an **opt-in cluster setting you must flip** even on v26.2 — an unusual thing to require for a fully mature GA feature — and the "table writes are blocked during backfill" limitation is explicitly called out as "currently being tracked," i.e., not yet fixed. **Practical recommendation for the hackathon:** treat it as GA-but-young. Verify against `SELECT version()` and the live docs page for whatever cluster you provision before you demo.

Sources: [Vector Indexes docs (v25.2)](https://www.cockroachlabs.com/docs/v25.2/vector-indexes), [Vector Indexes docs (v26.2)](https://www.cockroachlabs.com/docs/v26.2/vector-indexes), [What's New in v25.3](https://www.cockroachlabs.com/docs/releases/v25.3), [Introducing Vector Search with pgvector in CockroachDB](https://www.cockroachlabs.com/blog/vector-search-pgvector-cockroachdb/), [CockroachDB's 10-Year Release: Vector Indexing, Performance, and More](https://www.cockroachlabs.com/blog/cockroachdb-252-performance-vector-indexing/)

---

## 2. C-SPANN internals

### 2.1 Lineage

C-SPANN is Cockroach Labs' own distributed adaptation, borrowing from three external research lines:

- **[Microsoft SPANN](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/)** — contributes the core tree structure: a hierarchical K‑means partitioning of vectors into a disk-resident (not memory-resident) index, which is what makes billion-scale indexes tractable without huge RAM budgets.
- **Microsoft SPFresh** — contributes the incremental-update machinery: how to keep a partitioned ANN index fresh under continuous inserts/deletes without full rebuilds or serious quality degradation.
- **Google ScaNN** — contributes ideas around quantization for compact, fast-to-scan representations.

Cockroach Labs' design constraints, stated explicitly in their engineering writeup, were: no central coordinator, no large in-memory caches, real-time freshness, no write hotspots, and native shardability. Their solution: **the vector index is not a separate system — it's ordinary CockroachDB table data**, stored and replicated through the normal KV/range machinery, so it inherits distributed SQL's replication, rebalancing, and multi-region placement for free.

Sources: [Introducing Distributed Vector Indexing to CockroachDB](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/), [Real-Time Indexing for Billions of Vectors with CockroachDB](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/)

### 2.2 Structure: hierarchical K-means tree of partitions

- Vectors are grouped into **partitions** of "dozens to hundreds" of vectors, each represented by a centroid.
- Partitions form a wide, shallow **K-means tree** (typical fanout ~100). Example depths given by Cockroach Labs: **1M vectors → 3 levels**, **10B vectors → 5 levels**.
- Each partition is stored **as a self-contained unit in CockroachDB's key-value layer** — i.e., it maps onto ordinary KV rows/ranges, so CockroachDB's existing range-split/merge/rebalance logic distributes and scales the index automatically, the same way it would for any other table.
- The **root partition can be cached in memory** on a per-node basis to cut round-trips for the first hop of a search, but the bulk of the index lives on disk (Pebble/RocksDB storage), consistent with the "no large in-memory cache" requirement.

### 2.3 RaBitQ quantization — ~94% index-size reduction

RaBitQ is what keeps the index small enough to be practical at billion-vector scale:

- **Process:** apply a random orthogonal transform (spreads data skew evenly across dimensions) → mean-center relative to the partition centroid → normalize to unit length → binarize each dimension to a single bit (0 if negative, 1 if positive).
- Alongside the bit-packed vector, C-SPANN stores the **dot product between the quantized and original vector** and the **exact distance of the original vector from the centroid**, which lets it correct for quantization error during candidate re-ranking.
- **Query-time vectors** use a coarser **4-bit-per-dimension** quantization tuned for **SIMD** scan throughput.
- **Concrete size numbers given by Cockroach Labs:** an OpenAI-style embedding (1,536 dims, stored as 2-byte floats ≈ 3 KB/vector) compresses to **roughly 200 bytes** — a **~94% reduction**. This is the number to quote if you need a specific figure.

Source: [Real-Time Indexing for Billions of Vectors with CockroachDB](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/)

### 2.4 Freshness / incremental updates

C-SPANN is designed to avoid the classic ANN-index tradeoff (freshness vs. index quality vs. resource cost) via SPFresh-derived background maintenance:

- **Partition splits**: an oversized partition is divided into two balanced groups via K-means, done automatically without a central coordinator.
- **Partition merges**: undersized partitions (below `min_partition_size`) are consolidated with neighbors.
- **Vector relocation**: after a split/merge, individual vectors are reassigned to their new nearest centroid.
- **No central coordinator required** — any node in the cluster can serve both reads and writes against the index, consistent with CockroachDB's leaderless-from-the-client's-perspective architecture.
- Target: **99%+ recall** (measured as recall@k — the percentage of the true top‑k nearest vectors actually returned), tunable per query via beam size (§4).

### 2.5 Fit with Distributed SQL

Because each partition is literally a set of ordinary KV rows:

- It gets **automatically split, merged, and rebalanced** by CockroachDB's normal range machinery — no separate index-sharding logic to operate.
- It **replicates via Raft** like any other range, inheriting CockroachDB's usual survivability guarantees (see §5).
- **Prefix columns** on the vector index (see §3) let you build effectively **one K-means tree per tenant/owner/region** by including columns like `org_id` or `crdb_region` ahead of the `VECTOR` column in the index definition — combined with `REGIONAL BY ROW` tables, this makes the index itself geo-partitioned and locality-aware (see §5).
- **`EXPLAIN`** shows the vector index as a first-class plan node (`• vector search`) alongside ordinary index scans, `lookup join`, and `top-k` operators — it participates in the regular CockroachDB cost-based optimizer and distributed execution plan, not a bolted-on side path.

---

## 3. Exact SQL

### 3.1 The `VECTOR` data type

```sql
-- Add a vector column with an explicit, enforced dimension count
ALTER TABLE foo ADD COLUMN bar VECTOR(3);

-- Or inline in CREATE TABLE
CREATE TABLE items (
    category STRING,
    vector   VECTOR(3),
    INDEX (category)
);
```

- A `VECTOR` value is a fixed-length array of floats: `'[1.0, 0.0, 0.0]'`.
- Dimension count is **enforced** per column — inserting a mismatched-length array errors.
- No hard dimension-count ceiling is documented by Cockroach Labs; the type is pgvector-syntax-compatible, and pgvector itself caps at 16,000 dims for indexed columns (2,000 for HNSW specifically) — treat that as the practical ceiling, but verify against the target cluster since Cockroach Labs' own docs do not state a number.
- **Size guidance:** keep individual `VECTOR` values **under 1 MB** — beyond that, write amplification and other storage-layer effects cause "significant performance degradation." (For reference, even a 16,000-dim float4 vector is only ~64 KB, so this ceiling is not something typical embedding models will hit.)
- **Row size guardrail:** avoid large batched inserts of `VECTOR` rows in a single statement/transaction — insert vectors individually or in small batches; Cockroach Labs calls this out as a specific perf gotcha in both the `VECTOR` and `vector-indexes` docs pages.

Source: [`VECTOR` data type docs](https://www.cockroachlabs.com/docs/v26.2/vector)

### 3.2 Distance operators (pgvector-compatible)

| Operator | Metric | Opclass (for index acceleration) | Best for |
|---|---|---|---|
| `<->` | L2 (Euclidean) distance | `vector_l2_ops` (**default**) | Spatial/physical models where absolute position matters |
| `<=>` | Cosine distance | `vector_cosine_ops` | Directional/semantic similarity — the default choice for RAG-style text embeddings, especially models normalized or trained with cosine loss |
| `<#>` | Negative inner product | `vector_ip_ops` | When both magnitude and direction matter (scoring, preference modeling) |
| `=` / `<>` | Equality / inequality | — | Exact vector match/mismatch |

Not implemented as of v26.2: `vector_l1_ops` (L1/Manhattan), `bit_hamming_ops`, `bit_jaccard_ops`.

### 3.3 Enabling the feature

```sql
-- Required cluster setting (opt-in even on current stable)
SET CLUSTER SETTING feature.vector_index.enabled = true;

-- Required to create a vector index on a NON-EMPTY table
-- (backfill blocks writes on that table while it runs — see §6)
SET sql_safe_updates = false;
```

### 3.4 `CREATE VECTOR INDEX` syntax

```sql
-- Basic form
CREATE VECTOR INDEX ON items (embedding);

-- Inline at table creation
CREATE TABLE items (
    department_id INT,
    category_id   INT,
    embedding     VECTOR(1536),
    VECTOR INDEX (embedding)
);

-- With prefix columns (pre-filters the search space — see §3.5)
CREATE TABLE items (
    department_id INT,
    category_id   INT,
    embedding     VECTOR(1536),
    VECTOR INDEX (department_id, category_id, embedding)
);

-- Named index with an explicit opclass (cosine instead of default L2)
CREATE TABLE items (
    department_id INT,
    category_id   INT,
    embedding     VECTOR(1536),
    VECTOR INDEX embed_idx (embedding vector_cosine_ops)
);

-- Standalone CREATE VECTOR INDEX with tuning params
CREATE VECTOR INDEX ON items (category, embedding)
  WITH (min_partition_size = 16, max_partition_size = 128);
```

### 3.5 Prefix columns — the key pattern for multi-tenant agent memory

A vector index can lead with one or more non-vector "prefix" columns. The index is used **only** when every prefix column is constrained to specific value(s) via equality or `IN`:

```sql
-- Uses the index (equality on all prefix cols)
... WHERE department_id = 100 AND category_id = 200 ORDER BY embedding <-> $1 LIMIT 5;

-- Uses the index (IN with tuples)
... WHERE (department_id, category_id) IN ((100, 200), (300, 400)) ORDER BY embedding <-> $1 LIMIT 5;

-- Does NOT use the index (range predicate breaks prefix matching)
... WHERE department_id = 100 AND category_id >= 200 ORDER BY embedding <-> $1 LIMIT 5;
```

Effectively this builds **one K-means tree per distinct prefix-key value** — e.g., one per `org_id`/`agent_id` pair, which is exactly the shape you want for scoped agent-memory recall.

### 3.6 Full worked example (k-NN recall query + EXPLAIN)

```sql
CREATE TABLE items (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id INT NOT NULL,
    name        TEXT,
    embedding   VECTOR(512),
    VECTOR INDEX (customer_id, embedding)
);

-- k-NN query, scoped to one customer via the prefix column
SELECT id, name, embedding
FROM items
WHERE customer_id = 1
ORDER BY embedding <-> '[0.0064, -0.0087, ...]'::VECTOR(512)
LIMIT 3;
```

`EXPLAIN` on that query shows the vector index as a first-class operator:

```
distribution: local
vectorized: true

• top-k
│ estimated row count: 3
│ order: +column9
│ k: 3
│
└── • render
    │
    └── • lookup join
        │ table: items@items_pkey
        │ equality: (id) = (id)
        │ equality cols are key
        │
        └── • vector search
              table: items@items_customer_id_embedding_idx
              target count: 3
              prefix spans: [/1 - /1]
```

`prefix spans: [/1 - /1]` confirms the search was scoped to `customer_id = 1` before ANN traversal — i.e., prefix filtering happened *inside* the index walk, not as a post-filter.

Source: [Vector Indexes docs](https://www.cockroachlabs.com/docs/v26.2/vector-indexes) (this worked example, including the 156,541-row / 512-dim CLIP-embeddings dataset, is lifted directly from the official walkthrough)

---

## 4. Tuning: dimension limits, parameters, recall/latency tradeoffs

### 4.1 Index-build storage parameters (set at `CREATE VECTOR INDEX` time, via `WITH (...)`)

| Parameter | Meaning | Default | Range |
|---|---|---|---|
| `min_partition_size` | Vectors per partition before it's merged into a neighbor | `16` | 1 – 1024 |
| `max_partition_size` | Vectors per partition before it's split | `128` | must be ≥ 4× `min_partition_size`, up to 4096 |
| `build_beam_size` | How many K-means-tree branches are explored when assigning a new vector to a partition during build | `8` | Cockroach Labs explicitly recommends **not** tuning this — "offers little to no practical benefit" vs. tuning `vector_search_beam_size`, and can hurt build performance |

### 4.2 Query-time tuning

```sql
SET vector_search_beam_size = 16;   -- default 32
```

`vector_search_beam_size` controls how many partitions are explored **at each level** of the K-means tree during a search — this is the primary recall/latency knob at query time (analogous to `ef_search` in HNSW or `probes` in IVF).

### 4.3 Tradeoffs (from official guidance)

- **Higher recall, more cost:** increasing either `vector_search_beam_size` **or** partition size (`min_partition_size`/`max_partition_size`) increases the candidate set evaluated per query → better accuracy, but more CPU and higher read latency.
- **Partition size also affects writes:** larger partitions → fewer splits/merges → **faster inserts**. So there's a three-way tension between read accuracy, read latency, and write throughput, all governed by the same two knobs.
- **Interaction:** larger partitions let you *reduce* `vector_search_beam_size` without losing accuracy, since each partition already holds more candidates.
- **Filtering improves accuracy for free:** using a prefix-column-filtered query narrows the search space, which effectively improves accuracy at a given beam size — this is the official recommendation for improving recall on filtered queries, rather than just cranking beam size globally.
- No universal recommended settings are published — Cockroach Labs' explicit guidance is to **experiment against a representative dataset**, since "search accuracy is highly dependent on workload factors such as partition size, the number of `VECTOR` dimensions, how well the embeddings reflect semantic similarity, and how vectors are distributed in the dataset."

### 4.4 Published performance numbers (what's actually documented vs. not)

Concrete, sourced numbers:
- **~94% index-size reduction** via RaBitQ quantization (1,536-dim float embeddings: ~3 KB → ~200 bytes/vector).
- **Target 99%+ recall@k**, tunable.
- **"Low tens of milliseconds" search latency** with predictable network round-trip counts (qualitative, no specific p50/p99 published).
- Example query in the official walkthrough (512-dim, 156,541-row table, single node) returned in **14ms total (13ms execution / 1ms network)** — small-scale, not a load-test benchmark, but it's the one concrete latency number in the official docs.
- Separately, v25.2 overall (not vector-specific) shipped **~50% increased average throughput vs. 24.3** across nine general workloads, and **up to 4x faster restore** — general platform context, not vector-search-specific.

What is **not** published anywhere I could find: QPS-at-scale benchmarks, p99 latency under concurrent load, recall-vs-QPS curves, or any head-to-head numbers against Pinecone/pgvector/Milvus/etc. If the hackathon needs a benchmark story, plan to generate your own numbers rather than cite Cockroach Labs figures — they haven't published them yet.

---

## 5. The differentiator: vectors live *in* the transactional store

This is the pitch for agentic memory specifically, and it's architectural, not just a feature checkbox:

- **One ACID transaction, not two systems.** An agent's turn typically needs to (a) write the new memory row, (b) write/update its embedding, and (c) update related operational state (e.g., a session's `last_active_at`, a user's profile, a running token-budget counter) — all atomically. With a separate vector DB (Pinecone, Weaviate, Milvus, Qdrant), that's a dual-write: write the row to Postgres/CockroachDB, then write the embedding to the vector store, with no cross-system transaction. A crash between the two writes leaves a memory that's readable relationally but unsearchable semantically (or vice versa) — a silent data-integrity bug that's brutal to detect and repair for an "agent memory" system where trust in recall correctness matters.
- **No consistency gap between embeddings and operational data.** Because the `VECTOR` column and the `C-SPANN` index are ordinary CockroachDB table/index data under MVCC and serializable isolation, a read of a memory row and its embedding are always from the same consistent snapshot. There's no "index lag" window where a vector DB hasn't yet caught up to a relational update (or deletion — e.g., a GDPR delete of a user's data has to separately propagate to the vector store in a bolt-on architecture; here, `DELETE FROM episodic_events WHERE ...` removes the row *and* its index entry in the same transaction).
- **No separate reindexing pipeline.** In a bolt-on architecture, you run a CDC/ETL job to keep the vector store synced with the source of truth — extra infrastructure, extra latency, extra failure mode, extra thing to monitor at 3am. Here, the index updates transactionally and incrementally (SPFresh-derived splits/merges/relocations) as part of normal writes — "index maintenance" is just "normal CockroachDB background range management."
- **Inherits geo-partitioning and survivability for free.** Prefix columns on a vector index, combined with `REGIONAL BY ROW` tables, make the ANN index itself region-partitioned: a European user's memory rows *and* their vector-index entries are homed and queried from the EU region, a US user's from the US — satisfying data-domiciling requirements without a separate per-region vector-store deployment. And because each partition is stored as ordinary KV data, it replicates via Raft across the same failure domains as the rest of your schema — the same `SURVIVE ZONE FAILURE` / `SURVIVE REGION FAILURE` guarantees you already configured for the rest of your app apply automatically to the vector index, with no separate backup/DR story for "the vector database."
- **One connection pool, one query language, one operational surface.** Semantic recall (`ORDER BY embedding <-> $1`), structured filters (`WHERE org_id = $1 AND occurred_at > $2`), and joins to other operational tables (users, sessions, tool-call logs) compose in a single SQL statement, planned and executed by one distributed SQL engine — no cross-system query fan-out/merge logic in application code, no separate SDK, no separate auth/network boundary to secure.

Sources: [Introducing Distributed Vector Indexing to CockroachDB](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/), [Real-Time Indexing for Billions of Vectors with CockroachDB](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/), [How we built easy row-level data homing in CockroachDB with REGIONAL BY ROW](https://www.cockroachlabs.com/blog/regional-by-row/)

---

## 6. Honest limitations / preview-era caveats (current as of v26.2, verified against docs source)

Pulled directly from the current `known-limitations/vector-limitations.md` include and the `vector-indexes.md` page body:

- **Backfill blocks writes.** Adding a vector index to a non-empty table blocks `INSERT`/`UPSERT`/`UPDATE`/`DELETE` on that table while the backfill runs. Cockroach Labs' own docs flag this as "currently being tracked" — i.e., a known, unresolved rough edge, not a documentation oversight. Plan schema migrations (adding a vector index to an existing large table) for a maintenance window.
- **`IMPORT INTO` is not supported** on tables that already have a vector index. Workaround: import the vectors first, create the index after.
- **Three distance-metric opclasses are still missing:** `vector_l1_ops` (L1/Manhattan), `bit_hamming_ops`, `bit_jaccard_ops` — fine for typical float-embedding RAG use cases (L2/cosine/inner-product cover that), but a gap if you need binary/hash-vector search.
- **Filter acceleration is prefix-columns-only.** Any `WHERE` predicate that isn't an equality/`IN` match on a leading prefix column of the vector index falls back to a full/broader scan — there's no general secondary-predicate pushdown into the ANN traversal yet.
- **No index recommendations.** CockroachDB's usual "you might want an index here" advisor doesn't cover vector indexes yet — you have to reason about prefix columns and opclasses manually.
- **Large batched inserts degrade performance** — insert vectors individually/in small batches rather than one giant multi-row `INSERT` or bulk load, per explicit guidance on both the `VECTOR` and `vector-indexes` docs pages.
- **Opt-in cluster setting required even on "GA" versions**: `feature.vector_index.enabled` must be explicitly set — this is unusual for a feature no longer marked "preview," and worth flagging to the team as a signal the feature is still maturing operationally even if the docs no longer badge it as preview.
- **Root-partition caching, SIMD-accelerated scanning:** per the engineering blog, the root partition can be cached in memory and quantized vectors within a partition can leverage SIMD instructions for fast scanning — these read as *implemented* optimizations in the current architecture description, not "coming soon" items, but Cockroach Labs' own 2025-era writeup separately noted that partition merge/reassignment logic was "not yet fully implemented" at the time of the initial C-SPANN preview post. The current (v26.2) docs describe merging (`min_partition_size`) as a live, automatic behavior, which suggests this has since landed — but I found no explicit "merge support: now GA" changelog entry to cite chapter-and-verse. **Verify current merge/split behavior empirically** (e.g., watch partition counts under sustained delete-heavy load) if your demo depends on long-running index quality under churn.
- **No dimension-limit number published by Cockroach Labs.** pgvector's own ceiling (16,000 dims general / 2,000 dims for HNSW-indexed columns) is the closest reference point, but CockroachDB's docs don't restate a number — don't assume parity without testing your actual embedding dimensionality (1536 for `text-embedding-3-small`, 3072 for `text-embedding-3-large`, 1024/768 for many open models — all comfortably under any plausible limit).

---

## (A) Key facts & syntax cheat-sheet

**Status:** `VECTOR` type since v24.2 (preview). C-SPANN distributed index since v25.2 (preview, L2 only) → v25.3 added cosine + inner product (still preview) → v25.4 onward: preview banner removed from docs (treat as GA-but-young; `feature.vector_index.enabled` still required as of v26.2).

**Setup:**
```sql
SET CLUSTER SETTING feature.vector_index.enabled = true;
SET sql_safe_updates = false;   -- only needed to index a non-empty table
```

**Column:**
```sql
embedding VECTOR(1536)          -- dimension enforced; recommended value size < 1MB
```

**Operators:** `<->` L2 · `<=>` cosine · `<#>` negative inner product · `=` / `<>`

**Index:**
```sql
CREATE VECTOR INDEX ON items (org_id, embedding) WITH (min_partition_size=16, max_partition_size=128);
-- explicit metric:
CREATE VECTOR INDEX embed_idx ON items (embedding) USING vector_cosine_ops;  -- or inline: VECTOR INDEX embed_idx (embedding vector_cosine_ops)
```
Opclasses: `vector_l2_ops` (default) · `vector_cosine_ops` · `vector_ip_ops`

**Query (prefix-scoped k-NN):**
```sql
SELECT id, content FROM memory
WHERE org_id = $1 AND agent_id = $2         -- must be equality/IN to use the index
ORDER BY embedding <-> $3::VECTOR(1536)
LIMIT 8;
```

**Tuning:** `SET vector_search_beam_size = 32;` (session-level, default 32) · `min_partition_size`/`max_partition_size` at index-create time · leave `build_beam_size` alone.

**C-SPANN internals in one line:** hierarchical K-means tree of partitions (each partition = ordinary CockroachDB KV rows) + RaBitQ 1-bit quantization (~94% size cut) + SPFresh-derived incremental split/merge/relocate maintenance, no central coordinator, root partition cacheable in-memory, target 99%+ recall@k.

**Known numbers:** ~94% index-size reduction (1536-dim: ~3KB → ~200B/vector) · 99%+ recall@k target · "low tens of ms" qualitative latency · one documented example: 14ms total for a 3-row top-k query over 156,541 rows, 512 dims, single node.

---

## (B) Why this beats a separate vector DB for agent memory

- **Atomic writes across memory + operational state.** Insert a memory row, its embedding, and update session/user/token-budget counters in one `BEGIN...COMMIT` — impossible to get "half written" the way a dual-write to Postgres + Pinecone can.
- **No consistency gap / no index lag.** Reads always see embeddings and relational fields from the same MVCC snapshot; deletes (e.g., GDPR/right-to-be-forgotten) remove the row *and* its vector-index entry in the same transaction — no orphaned vectors in a separate store.
- **No CDC/ETL sync pipeline to build, monitor, and page on at 3am.** The vector index updates transactionally and incrementally as part of normal CockroachDB writes; there's no second system to keep in sync.
- **Geo-partitioning and data domiciling come free.** Prefix-column vector indexes + `REGIONAL BY ROW` mean an EU agent's memory and its ANN index entries are homed and queried in the EU, a US agent's in the US — without standing up per-region vector-store clusters.
- **Survivability is inherited, not re-engineered.** Vector index partitions are ordinary KV data replicated via Raft — the same zone/region survivability guarantees (`SURVIVE ZONE FAILURE`, `SURVIVE REGION FAILURE`) you already configured cover the vector index automatically; no separate backup/DR story for "the vector database."
- **One query, one engine, one ops surface.** Semantic recall, structured filters, and joins to other operational tables compose in a single SQL statement planned by one distributed SQL optimizer — no application-level fan-out/merge across a relational DB and a vector DB, no second SDK, no second network boundary to secure or bill for.
- **Fewer moving parts = faster to build in a hackathon.** One connection string, one schema, one transaction model — meaningfully less integration surface than wiring an app to both CockroachDB and a hosted vector DB.
