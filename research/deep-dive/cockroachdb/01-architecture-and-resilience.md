# CockroachDB Architecture & Resilience — The "Memory That Never Goes Down" Story

> Research brief for an agentic-memory system built on CockroachDB + AWS. Grounded in official Cockroach Labs documentation and engineering blog posts (2025–2026), current as of CockroachDB v25.2–v26.2. Every claim below is either sourced inline or flagged as general knowledge.

## Why this matters, in one paragraph

An AI agent's memory store has a specific shape: **constant small writes** (episodic memories, tool call logs, embeddings, state updates), **read-heavy retrieval** (semantic search, context reconstruction), a hard requirement that a **write acknowledged to the agent must never silently vanish**, and — for any agent system serving users in more than one place — a need to survive the loss of a machine, a rack, an AWS AZ, or an entire AWS region without the agent's "mind" going blank. CockroachDB's whole design center is exactly this: synchronously replicated, strongly consistent, horizontally scalable SQL that keeps serving reads and writes through failures most databases can't survive without a human paging in at 3 a.m.

---

## 1. Core Distributed SQL Architecture

CockroachDB is a single logical SQL database built from five layers that turn a SQL statement into replicated key-value operations ([Architecture Overview](https://www.cockroachlabs.com/docs/stable/architecture/overview)):

1. **SQL layer** — parses SQL, plans, and translates statements into key-value (KV) operations.
2. **Transaction layer** — makes multi-key KV operations atomic (ACID) via MVCC and a distributed commit protocol.
3. **Distribution layer** — presents the physically sharded KV keyspace as one giant sorted map, and routes each operation to the right node.
4. **Replication layer** — synchronously replicates every range of data via Raft consensus.
5. **Storage layer** — a local key-value store (Pebble, a Cockroach-Labs-built LSM engine) that handles the actual disk I/O on each node.

There is **no primary/master node**. Any node can serve as the SQL gateway for any client, and any node's DistSender can route a request to the correct data owner — this is what "distributed SQL" (as opposed to sharded Postgres) means in practice.

### Ranges: the unit of replication

- Data (including every index) is stored as an ordered key-value map, split into contiguous chunks called **ranges**.
- Default max range size is **512 MiB** (`range_max_bytes` = 536,870,912 bytes). When a range grows past that, CockroachDB splits it into two ranges automatically ([Architecture Overview](https://www.cockroachlabs.com/docs/stable/architecture/overview); [range_max_bytes default history](https://forum.cockroachlabs.com/t/default-size-of-a-range-in-cockroachdb/4666)).
- Ranges (not tables) are the unit CockroachDB rebalances, replicates, and load-balances across nodes.

### Replicas & Raft consensus

- By default, every range is replicated **3 ways** (replication factor, RF=3) onto different nodes ([Architecture Overview](https://www.cockroachlabs.com/docs/stable/architecture/overview)).
- Each range has its own independent **Raft consensus group**. A write to a range is only committed once a **quorum (majority)** of that range's replicas persist it — 2 of 3 for RF=3, 3 of 5 for RF=5. Failure tolerance follows `(replication factor − 1) / 2`, which is why 3 is the smallest RF that buys any fault tolerance at all ([Replication Layer](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)).
- Because consensus is per-range rather than per-cluster, a cluster runs **thousands of independent Raft groups** in parallel — this is the core mechanism behind horizontal scalability: no single log serializes the whole cluster's writes.

### Leaseholders: how reads/writes actually route

- Within each range's Raft group, one replica is the **leaseholder** — the only replica allowed to serve reads or propose writes for that range ([Replication Layer](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)).
- As of the current default (**leader leases**, introduced in v25.2 and covered below), the leaseholder and the Raft leader are architecturally forced to be the *same* replica except briefly during lease transfers — eliminating a historical class of "leader/leaseholder split" bugs under partition ([CockroachDB 25.2 resilience post](https://www.cockroachlabs.com/blog/cockroachdbs-resilience-25-2/); [Replication Layer](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)).
- Leaseholders can serve reads **without a Raft round-trip**: since a leaseholder's own writes already achieved consensus to get committed, its local data is already known-consistent, so reads bypass Raft entirely ([Replication Layer](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)).

### Routing: meta-ranges and DistSender

- CockroachDB keeps a **two-level index at the start of the keyspace** — `meta1` and `meta2` — mapping every range's key boundaries to the nodes holding its replicas. `meta1`'s own location is always known cluster-wide via gossip ([Distribution Layer](https://www.cockroachlabs.com/docs/stable/architecture/distribution-layer)).
- Every node's **DistSender** takes an incoming batch of KV operations, looks up (and locally caches) which node holds the leaseholder for each key range, and dispatches gRPC requests directly to the right leaseholder — in parallel across ranges when possible ([Distribution Layer](https://www.cockroachlabs.com/docs/stable/architecture/distribution-layer)).
- End-to-end write path ([Life of a Distributed Transaction](https://www.cockroachlabs.com/docs/stable/architecture/life-of-a-distributed-transaction)): client SQL → gateway node parses/plans → `TxnCoordSender` packages KV ops into `BatchRequests` → `DistSender` resolves each key to its leaseholder and dispatches in parallel → leaseholder checks its timestamp cache and latch manager, proposes the write to its Raft group → once a Raft quorum persists it, the leaseholder acknowledges → intents are asynchronously resolved to committed MVCC values, and the client gets its ack.

This means every SQL node is a valid entry point — critical for an agent framework that wants to point a connection pool at any node (or an AWS load balancer / RDS-Proxy-style pooler) without special-casing a "primary."

---

## 2. Consistency Model: What an Agent's Memory Write Actually Gets

This is arguably the most differentiated part of the story for agentic memory, because "the agent thinks it wrote something" and "the write is actually durable and visible to every future read" are not the same guarantee in most databases.

- **Isolation: SERIALIZABLE by default**, the strongest level in the ANSI SQL standard, permitting **zero concurrency anomalies** (no dirty reads, non-repeatable reads, phantom reads, write skew) ([Transaction Layer](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer); [Serializable, lockless, distributed](https://www.cockroachlabs.com/blog/serializable-lockless-distributed-isolation-cockroachdb/)).
- CockroachDB also offers **READ COMMITTED** as an opt-in, per-transaction isolation level (GA-track since v23.2/24.x) for workloads that want Postgres-style lower blocking at the cost of weaker anomaly guarantees; Cockroach Labs describes their READ COMMITTED as "stronger than Postgres's" because it's still the strongest isolation level that never throws client-visible serialization errors ([Read Committed Transactions](https://www.cockroachlabs.com/docs/v26.2/read-committed); [Isolation levels without the anomaly table](https://www.cockroachlabs.com/blog/232-read-committed-no-more-anomaly-tables/)).
- **MVCC + Hybrid Logical Clocks (HLC):** every value is versioned with an HLC timestamp (physical wall-clock time plus a logical counter). Reads see a consistent snapshot as of their transaction's timestamp; the leaseholder verifies a reading transaction's HLC time is greater than the MVCC value being read ([Transaction Layer](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer)).
- **Consistency level:** Cockroach Labs' own characterization is that the system sits "between serializable and linearizable" — precisely, it provides **single-key linearizability**: any set of transactions that touch overlapping keys are linearizable with respect to each other, while the system overall is SERIALIZABLE ([CockroachDB's consistency model](https://www.cockroachlabs.com/blog/consistency-model/)). In practice this means: once your agent's write to a memory row commits, any subsequent read of that same row (from anywhere in the cluster, immediately) is guaranteed to see it or something newer — no stale-read race on the exact fact you just wrote.
- **Write path guarantee (Parallel Commits):** commits are optimized so the client gets an ack after **one round of consensus** instead of two — writes go out in parallel across ranges, a STAGING transaction record is written, and once all intents report back as replicated, the client is told "committed" while intent-cleanup happens asynchronously in the background ([Transaction Layer](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer)). The client-visible commit is never returned before the underlying Raft quorum has durably persisted the write — this is the load-bearing fact for "agent memory write = actually safe."
- **Contention handling:** write-write/write-read conflicts go through a `TxnWaitQueue`; deadlocks are detected and resolved by randomly aborting one of the deadlocked transactions; conflicting transactions surface as retryable `40001` serialization errors that the client must retry (see §7, Limitations) ([Transaction Layer](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer)).

**Net guarantee for agent memory:** a `COMMIT` returned to an agent process means the memory record is durably persisted on a Raft-quorum of independent nodes (by default 2-of-3, physically separated), it will never be silently lost or rolled back except by a subsequent transaction, and any read issued after that commit — by that agent or a different agent instance reading the same row — will observe it. That is a materially stronger guarantee than what eventually-consistent memory stores (e.g., typical vector-DB-as-memory setups, or DynamoDB in default eventually-consistent-read mode) provide out of the box.

---

## 3. Multi-Region: Topology, Survival Goals, and the Exact Tradeoffs

### Table locality patterns

CockroachDB's multi-region SQL abstraction lets you pick a locality **per table** (even within the same database) ([Multi-Region Capabilities Overview](https://www.cockroachlabs.com/docs/stable/multiregion-overview); [Table Localities](https://www.cockroachlabs.com/docs/stable/table-localities)):

| Locality | Behavior | Reads | Writes |
|---|---|---|---|
| **REGIONAL (BY TABLE)** — default | Table's leaseholders pinned to one "home region" | Fast within home region; stale-but-fast from other regions via follower reads | Fast within home region; slower from elsewhere |
| **REGIONAL BY ROW** | Each *row* gets its own home region (a hidden `crdb_region` column) | Fast, consistent, local for rows in your region | Fast, consistent, local for rows in your region |
| **GLOBAL** | Replicas of the whole table live in every region; uses a **non-blocking transaction protocol** | Fast, consistent, **local in every region** | Slower everywhere — writes must replicate globally and pay a "commit-wait" |

- **REGIONAL BY ROW** is the natural fit for per-user/per-tenant agent memory: pin each user's (or each agent's) memory rows to the region they're actually running in, and both reads and writes stay low-latency and fully consistent, with no compromise ([Regional Tables](https://www.cockroachlabs.com/docs/stable/regional-tables); [choosing-a-multi-region-configuration](https://www.cockroachlabs.com/docs/stable/choosing-a-multi-region-configuration)).
- **GLOBAL** tables are for read-mostly, rarely-updated reference data with no natural home region — e.g., a shared prompt/policy table, tool-definition catalog, or model-config table an agent fleet reads everywhere but writes to rarely ([Global Tables](https://www.cockroachlabs.com/docs/stable/global-tables)). The write cost is real: "writes will incur higher latencies from any given region, since writes have to be replicated across every region... writes require a commit-wait step" ([Global Tables docs](https://www.cockroachlabs.com/docs/stable/global-tables)).

### Survival goals: ZONE vs. REGION

This is the resilience dial, set per database ([Multi-Region Survival Goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals)):

- **ZONE survivability (default):** replication factor 3, spread across availability zones. Tolerates loss of **one AZ** with zero downtime, zero added write latency. Does **not** guarantee availability if multiple AZs in the same region fail simultaneously, and does not survive a full region outage.
- **REGION survivability:** requires **≥3 database regions**. Replication factor jumps from 3 to **5**, distributed **2+2+1 across the regions**. Tolerates the loss of an **entire region** and stays fully available for reads and writes. Cost: "write latency will be increased by at least as much as the round-trip time to the nearest region; read performance will be unaffected" ([Multi-Region Survival Goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals)).
- Note the asymmetry: **two-region deployments cannot get REGION survivability** — Raft quorum arithmetic needs an odd number of regions (≥3) to survive one going dark without losing majority. A 2-region cluster has to rely on asynchronous DR tooling (Physical/Logical Cluster Replication, §5) instead of synchronous quorum, which reintroduces non-zero RPO.

### Locality-aware routing & follower reads

- **Follower reads** let *any* replica (not just the leaseholder) serve a read, at the cost of reading a slightly-stale-but-still-consistent snapshot — never dirty/uncommitted data ([Follower Reads](https://www.cockroachlabs.com/docs/stable/follower-reads)):
  - **Exact staleness reads** — `AS OF SYSTEM TIME follower_read_timestamp()`. The default offset is `kv.closed_timestamp.target_duration` (3s) + `propagation_slack` (1s) + `side_transport_interval` ≈ **4.2 seconds behind now** ([target_duration/propagation_slack defaults](https://github.com/cockroachdb/cockroach/pull/69775)).
  - **Bounded staleness reads** — `with_min_timestamp()`/`with_max_staleness()`, dynamically minimize staleness; restricted to single-statement, single-row-ish queries, but tolerate replication lag better and improve availability during partitions.
  - **Strong (global) follower reads** — only for GLOBAL tables, via the non-blocking transaction protocol; these are *not stale at all* — full current consistency served from a local replica.
- **Topology patterns** ([Topology Patterns Overview](https://www.cockroachlabs.com/docs/stable/topology-patterns)):
  - *Basic Production* (single region, 3 AZs): fast reads/writes, survives 1 AZ loss.
  - *Regional (follower) reads pattern*: fast regional historical reads, slower cross-region writes — Cockroach Labs' recommended default for multi-region read-heavy access.
  - *Follow-the-workload*: fast reads in the currently-active region, slower elsewhere; no pinning. Docs explicitly warn: with default follow-the-workload and no locality tuning, "latency will likely be unacceptably high" for broadly distributed deployments — this is the one honest caveat CockroachDB documents about naive multi-region defaults.
  - *Geo-partitioned replicas/leaseholders* (via REGIONAL BY ROW + explicit locality): the pattern used for compliance/data-residency and lowest possible latency per row.
- **Clock configuration for multi-region:** CockroachDB recommends lowering `--max-offset` to **250ms** (from the 500ms default) in multi-region clusters specifically to reduce write latency for global/multi-region transactions ([choosing-a-multi-region-configuration](https://www.cockroachlabs.com/docs/stable/choosing-a-multi-region-configuration)).

---

## 4. Resilience / High Availability

### Automatic failover and self-healing

- Node, AZ, and (with REGION survivability) region loss are all handled **without operator intervention**: the cluster detects the failure, elects new Raft leaders/leaseholders for affected ranges, and reroutes traffic — no failover script, no DNS cutover, no promoting a replica by hand.
- **Leader leases (current default architecture, GA'd v25.2):** unify the Raft leader and the range leaseholder into a single role, backed by a new decentralized failure detector called the **Liveness Fabric / Store Liveness**, which detects node- and network-level failures (including asymmetric partitions) without relying on a single centralized liveness range ([25.2 resilience blog](https://www.cockroachlabs.com/blog/cockroachdbs-resilience-25-2/); [Replication Layer](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)). Measured results reported by Cockroach Labs:
  - **Network partitions heal in under 20 seconds** (vs. depending on a single node-liveness heartbeat range under the old epoch-lease design).
  - **Outages caused by liveness failures last under 1 second.**
  - Steady-state performance within **<1% of legacy epoch-based leases**.
  - In 25.2 vs 25.1 internal benchmarks: multi-region (15-node) SQL latency during a resilience test dropped from ~20ms to **~2.24ms**; single-region (9-node) dropped from ~3ms to **~1.32ms**; node restarts recovered in **~30 seconds** with negligible latency spikes ([25.2 resilience blog](https://www.cockroachlabs.com/blog/cockroachdbs-resilience-25-2/)).
  - Older/legacy figure still cited in Cockroach Labs marketing: the general "failure to new-leaseholder" cycle "should complete within a few seconds," with an average-case **RTO around 4.5 seconds** and worst-case liveness-expiration bound of **under 9 seconds** under the epoch-lease model ([RPO/RTO blog](https://www.cockroachlabs.com/blog/demand-zero-rpo/)) — leader leases in 25.2+ meaningfully improve on this baseline.

### What happens on loss, concretely

| Failure | Requires | Result |
|---|---|---|
| Single node | RF=3 (default) | Zero downtime, zero data loss; ranges re-replicate to a healthy node in the background |
| Single AZ | ZONE survivability, ≥3 AZs, RF=3 | Fully available for reads/writes; no added latency |
| Multiple AZs in one region simultaneously | — | Not guaranteed available under ZONE survivability |
| Entire region | REGION survivability, ≥3 regions, RF=5 (2+2+1) | Fully available for reads/writes; writes pay ≥1 cross-region RTT |
| Region, with only 2 regions configured | — | **Cannot** stay available via synchronous quorum (no majority possible) — must fail over via async DR (PCR/LDR), with non-zero RPO |

Source: [Data Resilience](https://www.cockroachlabs.com/docs/stable/data-resilience), [Multi-Region Survival Goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals).

### RPO/RTO by strategy, and the "zero data loss" claim precisely

Per the official Data Resilience doc, official RPO/RTO figures by strategy:

| Strategy | RPO | RTO |
|---|---|---|
| Single-region synchronous replication (Raft, default) | **0 seconds** | Seconds (brief latency spike, ~1–9s) during failover |
| Multi-region synchronous replication (REGION survival goal) | **0 seconds** | Seconds during failover |
| Logical Data Replication (async, row-level) | ~0.5 seconds | Depends on application failover time |
| Physical Cluster Replication (async, byte-level, cross-cluster) | Tens of seconds | Seconds to minutes |
| Backup & Restore | ≥5 minutes | Minutes to hours |

Source: [Data Resilience](https://www.cockroachlabs.com/docs/stable/data-resilience), [Disaster Recovery Overview](https://www.cockroachlabs.com/docs/stable/disaster-recovery-overview).

**The zero-RPO claim's exact conditions** ([RPO/RTO blog](https://www.cockroachlabs.com/blog/demand-zero-rpo/); [Data Resilience](https://www.cockroachlabs.com/docs/stable/data-resilience)):
- It holds **only within a synchronously-replicated Raft quorum** — i.e., a committed write already lives on a majority of replicas across independent failure domains (nodes, AZs, or with REGION survivability, regions).
- It does **not** hold across the boundary of an asynchronous replication mechanism (Physical/Logical Cluster Replication) — those are explicitly non-zero RPO (tens of seconds / ~0.5s respectively) because replication to the standby cluster happens after the primary already acknowledged the write.
- It does **not** hold if you lose **more than a minority** of replicas simultaneously (e.g., 2 of 3 nodes in an RF=3 range, or 2 whole regions in an RF=5/REGION-survival setup) — that's a quorum loss, and by definition you cannot make progress or guarantee no loss beyond what was already durably quorum-committed.
- A 2-region topology cannot achieve zero-RPO region-failure protection at all via synchronous replication (no odd-region majority) — it needs a 3rd region, or must accept async DR's non-zero RPO.

---

## 5. Disaster Recovery: Backups, PITR, Changefeeds, PCR/LDR

Four DR primitives, from "belt-and-suspenders archival" to "near-real-time standby":

1. **Backup & Restore** ([Backup and Restore Overview](https://www.cockroachlabs.com/docs/stable/backup-and-restore-overview)):
   - `BACKUP INTO 's3://bucket/path?AUTH=implicit' AS OF SYSTEM TIME '-10s'` for full backups; `BACKUP INTO LATEST IN '...'` for incrementals.
   - With `revision_history`, backups capture every MVCC revision in the window, enabling **point-in-time recovery (PITR)**: `RESTORE ... AS OF SYSTEM TIME <ts>` rolls the target back to any point in that window — critical for recovering from application-level corruption (e.g., a buggy agent overwriting/deleting memory rows), which replication cannot protect against since replication faithfully replicates mistakes too ([Take Backups with Revision History and Restore from a Point-in-time](https://www.cockroachlabs.com/docs/stable/take-backups-with-revision-history-and-restore-from-a-point-in-time)).
   - `RESTORE` only ever restores the *latest* state as of the chosen timestamp — it is not a live/incremental-apply operation, so RTO scales with data size (minutes to hours).
   - Cross-version restore is supported same-major-version or into the **next** major version ([Restoring Backups Across Versions](https://www.cockroachlabs.com/docs/stable/restoring-backups-across-versions)).
   - Scheduled backups: `CREATE SCHEDULE ... RECURRING '@daily' ...` for automated, ongoing coverage.

2. **Changefeeds (CDC)** — stream row-level changes to Kafka, cloud storage, or a webhook sink. Not itself a backup, but the standard way to build a **true external archival copy** independent of CockroachDB, or to feed downstream systems (e.g., a separate long-term/cold memory store, an analytics warehouse, or an audit log) in near-real time ([search results synthesis](https://oneuptime.com/blog/post/2026-02-02-cockroachdb-backup-restore/view)).

3. **Physical Cluster Replication (PCR)** — byte-level, asynchronous, continuous replication of an *entire cluster* to a passive standby cluster (can be cross-region or cross-cloud). Manual failover; RPO in the tens-of-seconds range; RTO seconds-to-minutes; protects against node/AZ/region loss on the primary and is the primitive for 2-region DR topologies that can't get synchronous REGION survivability ([Disaster Recovery Overview](https://www.cockroachlabs.com/docs/stable/disaster-recovery-overview); [Mastering PCR for DR](https://www.cockroachlabs.com/blog/disaster-recovery-physical-cluster-replication-cockroachdb/)).

4. **Logical Data Replication (LDR)** — row-level, async replication, supports more flexible/active-active topologies than PCR; RPO ~0.5s ([Data Resilience](https://www.cockroachlabs.com/docs/stable/data-resilience)).

**Monitoring**: `SELECT * FROM crdb_internal.jobs WHERE job_type IN ('BACKUP','RESTORE')` and `PAUSE/RESUME/CANCEL JOB <id>` for changefeeds/backups in flight.

---

## 6. Scale: Horizontal Growth and Write Throughput

- **No master node, no manual sharding.** Adding a node is `cockroach start --join=...`; the cluster automatically rebalances ranges onto it. Removing capacity is `cockroach node decommission`, which drains replicas off before the node leaves.
- **Linear scaling, benchmarked:** on TPC-C, CockroachDB 20.2 sustained **1.7 million tpmC (transactions per minute) across 140,000 warehouses** on an 81-node cluster of `c5d.9xlarge` (36 vCPU) instances, with throughput scaling roughly linearly as nodes/warehouses were added together ([CockroachDB 20.2 performance](https://www.cockroachlabs.com/blog/cockroachdb-performance-20-2/); [Performance Benchmarking with TPC-C](https://www.cockroachlabs.com/docs/stable/performance-benchmarking-with-tpcc-large)). Because there's no single write-master, throughput doesn't plateau the way single-primary architectures do.
- **Per-range write ceiling:** a single range tops out around **~1,000 writes/sec** before per-range latency rises — this is the practical unit you need to keep spreading writes across as an agent-memory workload grows (general CockroachDB operational guidance; consistent with [Understand Hotspots](https://www.cockroachlabs.com/docs/stable/understand-hotspots)).
- **Capacity rule of thumb:** roughly **500–1,000 simple QPS per vCPU**, workload-dependent — useful for back-of-envelope sizing an agent fleet's memory-write load against a target cluster size.
- **Avoiding write hotspots for high-throughput agent writes** — directly relevant since "agents write constantly":
  - Sequential/monotonic primary keys (auto-increment IDs, or naive `created_at`-prefixed keys) funnel all new inserts into the tail of one range/leaseholder, capping write throughput at what one node can do regardless of cluster size.
  - Standard fix: **UUID primary keys** (random distribution across ranges) or well-designed **multi-column composite keys**.
  - If a monotonic key is unavoidable (e.g., you want time-ordered memory IDs for cheap range scans), use a **hash-sharded index** to distribute the sequential write load across ranges while still supporting ordered access patterns ([Hash-sharded Indexes](https://www.cockroachlabs.com/docs/stable/hash-sharded-indexes); [Understand Hotspots](https://www.cockroachlabs.com/docs/stable/understand-hotspots)).
  - For very large batch loads/backfills (e.g., bulk-importing prior conversation history into memory), CockroachDB guidance recommends splitting into parallel threads over **disjoint** key ranges — never overlapping keys, which causes serialization retries.

---

## 7. Honest Limitations and Tradeoffs

Being accurate here matters more than being persuasive:

- **Cross-region synchronous writes are slower, physically.** Under REGION survivability or with GLOBAL tables, every write must achieve consensus (or commit-wait) across geographically separated nodes — the floor is the speed of light plus network stack overhead. Docs are explicit: write latency increases "by at least as much as the round-trip time to the nearest region" for REGION survival goal, and GLOBAL tables pay a commit-wait tax on every write regardless of survival goal ([Multi-Region Survival Goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals); [Global Tables](https://www.cockroachlabs.com/docs/stable/global-tables)). There is no way around this without weakening consistency — it's the CAP-theorem-adjacent price of the zero-RPO guarantee.
- **Contention is real under SERIALIZABLE.** Concurrent transactions hitting overlapping keys can throw retryable `40001` serialization errors; applications must implement retry-with-backoff logic (client-side, since CockroachDB won't silently downgrade your isolation for you). High-contention hot rows (e.g., a single "global agent state" counter row updated by every agent instance) will bottleneck regardless of cluster size — the fix is workload/schema redesign, not more nodes ([Transaction Layer](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer)).
- **Hotspots are a schema-design problem, not something the database eliminates automatically.** As above — sequential keys, small "hot" reference tables, or one heavily-contended row can all cap throughput on a single range/leaseholder even in an otherwise huge cluster.
- **Two-region deployments cannot get synchronous zero-RPO region failover** — Raft majority arithmetic requires ≥3 regions for that. Many real-world deployments (e.g., "just us-east-1 + us-west-2") are structurally limited to async DR (PCR/LDR) for region-level protection, with RPO in the 0.5s–tens-of-seconds range, not zero.
- **Cost scales with resilience.** RF=3 (default/ZONE survivability) means 3x storage for every byte written; REGION survivability means RF=5, i.e., **5x storage** plus cross-region network egress on every write. A standby cluster for PCR effectively doubles infrastructure spend. This is a real, quantifiable cost an agent-memory system's storage budget needs to account for — "3–5x the raw bytes" is the multiplier to plan against, not the nominal memory-record size.
- **Operational correctness matters.** The multi-region abstractions (survival goals, table localities) only deliver their advertised latency/consistency properties if locality flags and zone configs are set correctly on every node; docs explicitly warn that the naive default (follow-the-workload, no locality tuning) in a broadly distributed deployment can produce "unacceptably high" latency ([Topology Patterns Overview](https://www.cockroachlabs.com/docs/stable/topology-patterns)).
- **Clock dependency.** As an HLC-based system, CockroachDB needs NTP-disciplined clocks and a configured `--max-offset` (default 500ms, recommended 250ms for multi-region); large clock skew beyond `--max-offset` can crash a node rather than silently corrupt data — a fail-safe, but an operational dependency worth knowing about for AWS deployment (chrony/NTP on every EC2 instance is not optional).

---

## (A) Key Metrics & Guarantees

- **Replication factor:** 3 by default (ZONE survivability); 5, distributed 2+2+1 across ≥3 regions, for REGION survivability. [[Multi-Region Survival Goals]](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals)
- **Default range size:** 512 MiB per range; ranges split automatically on growth. [[Architecture Overview]](https://www.cockroachlabs.com/docs/stable/architecture/overview)
- **Isolation:** SERIALIZABLE by default — the strongest ANSI SQL isolation level, zero concurrency anomalies; READ COMMITTED available as an opt-in. [[Transaction Layer]](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer)
- **Consistency:** single-key linearizability + cluster-wide serializability — a committed write is immediately visible to any subsequent read of that key, anywhere in the cluster. [[Consistency Model]](https://www.cockroachlabs.com/blog/consistency-model/)
- **RPO for synchronous replication (single- or multi-region with REGION survival goal): 0 seconds — genuine zero data loss**, conditioned on not losing more than a minority of replicas at once. [[Data Resilience]](https://www.cockroachlabs.com/docs/stable/data-resilience)
- **RTO:** network partitions heal in **under 20 seconds**; liveness-failure outages last **under 1 second**; average failover historically cited around **4.5 seconds**, worst case **under 9 seconds** (pre-leader-lease baseline). [[25.2 Resilience]](https://www.cockroachlabs.com/blog/cockroachdbs-resilience-25-2/) [[RPO/RTO blog]](https://www.cockroachlabs.com/blog/demand-zero-rpo/)
- **AZ loss:** zero downtime, zero added latency, under default ZONE survivability with ≥3 AZs. **Region loss:** zero downtime with REGION survivability (≥3 regions, RF=5), write latency increases by ≥1 cross-region RTT. [[Data Resilience]](https://www.cockroachlabs.com/docs/stable/data-resilience)
- **Async DR fallback (2-region or cross-cluster):** Physical Cluster Replication, RPO tens of seconds, RTO seconds-to-minutes; Logical Data Replication, RPO ~0.5s. Backup/Restore: RPO ≥5 min, RTO minutes-to-hours. [[Disaster Recovery Overview]](https://www.cockroachlabs.com/docs/stable/disaster-recovery-overview)
- **Follower reads staleness:** ~4.2 seconds behind present by default (exact staleness); bounded-staleness reads minimize this dynamically; GLOBAL-table strong follower reads have **zero** staleness. [[Follower Reads]](https://www.cockroachlabs.com/docs/stable/follower-reads)
- **Scale benchmark:** 1.7M tpmC across 140,000 TPC-C warehouses on 81 nodes, roughly linear scaling with node count. [[TPC-C Performance]](https://www.cockroachlabs.com/blog/cockroachdb-performance-20-2/)
- **Per-range write ceiling:** ~1,000 writes/sec before latency rises — the reason to avoid sequential keys in a high-write agent-memory schema.

---

## (B) Why This Matters for Agentic Memory

- **A committed memory write is provably durable, not "probably durable."** Because commit acknowledgment only happens after Raft-quorum persistence across independently-failing nodes (and optionally AZs/regions), an agent can trust that once it gets a commit back, that memory survives a machine dying under it mid-write — no eventual-consistency window where a "remembered" fact quietly disappears.
- **Read-after-write is guaranteed, cluster-wide, immediately.** Single-key linearizability means an agent (or a *different* agent instance/replica of the same agent) that reads a memory record right after another instance wrote it will never see a stale/absent version — essential for multi-agent systems sharing memory, where "did the other agent already write this?" races are otherwise a classic source of duplicated tool calls or conflicting plans.
- **Zero-RPO synchronous replication means agent memory survives infrastructure failure without a DR runbook.** Node, AZ, or (with REGION survivability) full-region loss is handled automatically, in seconds, with zero data loss and no human failover step — which matters because an agent's "memory going down" mid-task is a much worse failure mode than a stateless API blip: it can corrupt the agent's understanding of what it has and hasn't already done.
- **REGIONAL BY ROW gives per-agent/per-user data locality without sacrificing consistency** — pin each user's or each agent instance's memory to the region it actually runs in for local-speed reads/writes, while still getting full ACID guarantees, instead of the typical multi-region tradeoff of picking either low latency *or* strong consistency.
- **Horizontal scaling with no master node matches an agent fleet's write pattern.** As agent concurrency grows (more agents, more tool calls, more memory writes per second), CockroachDB scales out linearly by adding nodes rather than hitting a single-writer ceiling — provided the memory schema avoids sequential-key hotspots (use UUID/composite memory-record IDs), which is a cheap design choice to get right from day one.

---

### Primary sources consulted
- [Architecture Overview](https://www.cockroachlabs.com/docs/stable/architecture/overview)
- [Replication Layer](https://www.cockroachlabs.com/docs/stable/architecture/replication-layer)
- [Transaction Layer](https://www.cockroachlabs.com/docs/stable/architecture/transaction-layer)
- [Distribution Layer](https://www.cockroachlabs.com/docs/stable/architecture/distribution-layer)
- [Life of a Distributed Transaction](https://www.cockroachlabs.com/docs/stable/architecture/life-of-a-distributed-transaction)
- [CockroachDB's Consistency Model](https://www.cockroachlabs.com/blog/consistency-model/)
- [Serializable, lockless, distributed: Isolation in CockroachDB](https://www.cockroachlabs.com/blog/serializable-lockless-distributed-isolation-cockroachdb/)
- [Read Committed Transactions](https://www.cockroachlabs.com/docs/v26.2/read-committed)
- [Multi-Region Capabilities Overview](https://www.cockroachlabs.com/docs/stable/multiregion-overview)
- [Table Localities](https://www.cockroachlabs.com/docs/stable/table-localities) / [Regional Tables](https://www.cockroachlabs.com/docs/stable/regional-tables) / [Global Tables](https://www.cockroachlabs.com/docs/stable/global-tables)
- [Multi-Region Survival Goals](https://www.cockroachlabs.com/docs/stable/multiregion-survival-goals)
- [Choosing a Multi-Region Configuration](https://www.cockroachlabs.com/docs/stable/choosing-a-multi-region-configuration)
- [Topology Patterns Overview](https://www.cockroachlabs.com/docs/stable/topology-patterns)
- [Follower Reads](https://www.cockroachlabs.com/docs/stable/follower-reads)
- [Data Resilience](https://www.cockroachlabs.com/docs/stable/data-resilience)
- [Disaster Recovery Overview](https://www.cockroachlabs.com/docs/stable/disaster-recovery-overview)
- [RPO and RTO: getting to zero downtime and zero data loss](https://www.cockroachlabs.com/blog/demand-zero-rpo/)
- [CockroachDB 25.2 resilience](https://www.cockroachlabs.com/blog/cockroachdbs-resilience-25-2/)
- [Mastering Physical Cluster Replication for Disaster Recovery](https://www.cockroachlabs.com/blog/disaster-recovery-physical-cluster-replication-cockroachdb/)
- [Backup and Restore Overview](https://www.cockroachlabs.com/docs/stable/backup-and-restore-overview) / [Take Backups with Revision History and Restore from a Point-in-time](https://www.cockroachlabs.com/docs/stable/take-backups-with-revision-history-and-restore-from-a-point-in-time) / [Restoring Backups Across Versions](https://www.cockroachlabs.com/docs/stable/restoring-backups-across-versions)
- [CockroachDB 20.2 Performance / TPC-C](https://www.cockroachlabs.com/blog/cockroachdb-performance-20-2/) / [Performance Benchmarking with TPC-C](https://www.cockroachlabs.com/docs/stable/performance-benchmarking-with-tpcc-large)
- [Understand Hotspots](https://www.cockroachlabs.com/docs/stable/understand-hotspots) / [Hash-sharded Indexes](https://www.cockroachlabs.com/docs/stable/hash-sharded-indexes)
