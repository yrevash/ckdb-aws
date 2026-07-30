# Deep Dive — CockroachDB vs. the Field (for Agentic Memory)

A thorough, source-cited research pass: understand CockroachDB completely, map every serious
competitor, then decide **where we win** with measurable goals. Produced by 7 parallel research
agents + a synthesis.

## Read the synthesis first
➡️ **[`08-synthesis-where-cockroachdb-wins.md`](./08-synthesis-where-cockroachdb-wins.md)** — the
decision doc: comparison matrix, the axes CockroachDB genuinely wins (and loses), the strategic
wedge, and concrete **metrics + goals** for our build. Everything else is the evidence behind it.

## CockroachDB deep dive (`cockroachdb/`)
| File | Covers |
|------|--------|
| [`01-architecture-and-resilience.md`](./cockroachdb/01-architecture-and-resilience.md) | Ranges/Raft/leaseholders, serializable + linearizable consistency, multi-region topologies, **RPO=0 / RTO<9s** failover, DR, scale. |
| [`02-vector-search-and-cspann.md`](./cockroachdb/02-vector-search-and-cspann.md) | Vector type + **C-SPANN** internals (RaBitQ ~94%), exact `CREATE VECTOR INDEX` SQL, distance ops, recall/latency, GA status, limits. |
| [`03-agent-toolchain.md`](./cockroachdb/03-agent-toolchain.md) | Managed **MCP Server** (OAuth2.1 / service-account, read-only default), **ccloud CLI**, **Agent Skills** (33 skills), LangChain integration — with setup gotchas. |
| [`04-memory-data-modeling.md`](./cockroachdb/04-memory-data-modeling.md) | Concrete schemas for episodic/semantic/procedural/working memory, **bitemporal facts**, changefeeds→Lambda, TTL decay, `REGIONAL BY ROW`. |

## Competitor mapping (`competitors/`)
| File | Covers |
|------|--------|
| [`01-vector-databases.md`](./competitors/01-vector-databases.md) | Pinecone, Weaviate, Qdrant, Milvus/Zilliz, Redis, pgvector, Mongo/OpenSearch — the **consistency-gap** analysis. |
| [`02-agent-memory-frameworks.md`](./competitors/02-agent-memory-frameworks.md) | Mem0, Zep/Graphiti, Letta, LangMem, MemoryOS, Cognee — benchmarks + which can run **on** CockroachDB. |
| [`03-distributed-sql-and-cloud-memory-stores.md`](./competitors/03-distributed-sql-and-cloud-memory-stores.md) | Aurora, Aurora DSQL, Neon, Yugabyte, Spanner, DynamoDB, **Bedrock AgentCore Memory** (the one to beat). |

## The three headline conclusions
1. **We only truly compete with "database-as-memory" options** — memory frameworks are complements
   (they can sit *on* CockroachDB); vector DBs win raw speed but lose on the consistency gap.
2. **CockroachDB's uncatchable axes:** single-store (vectors + operational data in one ACID txn),
   read-your-own-writes at global scale, and RPO=0 automatic region survival. **Don't** compete on
   raw ANN throughput (specialists win) or zero-ops memory-only convenience (AgentCore wins).
3. **The winning wedge:** build a use case where the agent must **read operational data → remember →
   act on it**, consistently, and **survive a live region failure**. That neutralizes AWS-native
   AgentCore Memory and puts CockroachDB's moat on camera.
