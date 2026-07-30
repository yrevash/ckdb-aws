# Agent-Memory Frameworks — Competitive / Adjacent Landscape

> Reference doc for the "CockroachDB as the best store for agentic memory" hackathon positioning.
> Scope: the **software layers** that implement agent memory (extraction, consolidation, retrieval, temporal reasoning) — the logic that sits **on top of a database**. These are mostly **complements**, not competitors, to CockroachDB. The critical lens throughout: *what durable backing store does each one require, and could CockroachDB be that store?*
>
> Sourced from 2025–2026 official docs, arXiv papers, and benchmark write-ups. Last updated 2026-07-30.

---

## 0. TL;DR framing

An "agent memory framework" is the layer that decides **what to remember, how to structure it, and what to retrieve**. Underneath, almost every one of them writes to a **durable database** — a vector store, a graph DB, and/or a relational DB. That durable layer is exactly where CockroachDB plays. See the analysis section at the end.

Two facts to anchor on:
- **Most of these frameworks are storage-pluggable.** Mem0, LangMem, Cognee, and Graphiti/Zep all abstract the backend; several already support **Postgres / pgvector**, which is the wire-and-SQL surface CockroachDB is compatible with.
- **The benchmark leaders (Zep, Mem0) are memory logic, not databases.** They still need Neo4j/FalkorDB/Postgres/Qdrant underneath. CockroachDB can be that consolidated, multi-region, strongly-consistent tier.

---

## 1. Mem0

**Architecture.** Vector-first memory layer with an optional graph variant (`Mem0g` / "Graph Memory"). Two-phase pipeline: an **extraction phase** (an LLM reads the latest exchange + a rolling summary + the *m* most recent messages and emits concise candidate memories) and an **update phase** (LLM-based conflict resolution decides ADD / UPDATE / DELETE / NOOP against existing memories). The graph variant adds an entity/relationship-triplet knowledge graph over the same data, supporting multi-hop traversal and entity-centric retrieval. ([Mem0 research](https://mem0.ai/research), [Emergent Mind: Mem0](https://www.emergentmind.com/topics/mem0), [Dwarves breakdown](https://memo.d.foundation/breakdown/mem0))

**Memory types.** Primarily **semantic** (facts, preferences) plus a working/**episodic**-style rolling window; procedural memory is not a first-class primitive. The graph variant adds relational/semantic structure.

**Backing store (critical).** Highly pluggable. Open-source `Memory` class supports **20+ vector backends**: Qdrant (default), **PGVector/Postgres**, Chroma, Pinecone, Redis, FAISS, Weaviate, Milvus, MongoDB, Elasticsearch, OpenSearch, **Supabase**, Azure AI Search, Valkey, Amazon S3 Vectors, Databricks, Turbopuffer, and more. Swapping backends is a **config change, not a code change**. On the graph side it uses **Neo4j** (or Memgraph). ([Mem0 vector DB overview](https://docs.mem0.ai/components/vectordbs/overview), [DeepWiki: storage backends](https://deepwiki.com/mem0ai/mem0/5-vector-stores), [Qdrant × Mem0](https://qdrant.tech/documentation/frameworks/mem0/)) → **PGVector support means CockroachDB is a plausible drop-in target for the relational/vector tier.**

**Key concepts.** LLM extraction pipeline; conflict-resolution update loop; token efficiency as a headline metric; graph memory as an add-on rather than the core.

**Benchmarks (numbers).**
- **LoCoMo:** Mem0's ECAI 2025 paper (arXiv:2504.19413) reports a **26% relative accuracy gain over OpenAI's memory**, with **~91% lower p95 latency** and **~90% fewer tokens**. A newer token-efficient algorithm reports **92.5 on LoCoMo** at **<7,000 tokens/retrieval**. ([Mem0 research](https://mem0.ai/research))
- **LongMemEval:** In an independent comparison, **Mem0 scored 49.0%** vs **Zep 63.8%** (GPT-4o). ([Vectorize: Mem0 vs Zep](https://vectorize.io/articles/mem0-vs-zep))
- **Caveat:** Zep's team disputes Mem0's SOTA claim, arguing LoCoMo is near-saturated/flawed and that Mem0's Zep implementation was incorrect. ([Zep: "Is Mem0 really SOTA?"](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/))

**Strengths.** Easiest on-ramp; huge backend flexibility; strong token/latency story; large adoption; managed cloud + OSS. **Weaknesses.** Weak native temporal reasoning (vector-first, graph bolted on); LoCoMo-centric marketing that is contested; loses to graph-native systems on hard temporal benchmarks.

---

## 2. Zep / Graphiti (temporal knowledge graph)

**Architecture.** **Graph-first, temporally-aware.** Zep is a memory-layer *service*; its engine **Graphiti** is a real-time **temporal knowledge graph** that fuses unstructured conversation and structured business data into entity/relationship nodes and edges, incrementally (no batch recompute). ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956), [getzep.com/platform/graphiti](https://www.getzep.com/platform/graphiti/))

**Memory types.** Semantic + episodic + relational, unified in one graph; strong **temporal** dimension. Working memory is handled by retrieval into the prompt.

**Backing store (critical).** Graphiti runs on a **graph database**: **Neo4j** (primary, v5.26+), **FalkorDB** (Redis-protocol), **Amazon Neptune** (+ OpenSearch for full-text), and **Kuzu** (embedded, now deprecation-warned). ([Graphiti README](https://github.com/getzep/graphiti/blob/main/README.md), [Neo4j config](https://help.getzep.com/graphiti/configuration/neo-4-j-configuration), [FalkorDB config](https://help.getzep.com/graphiti/configuration/falkor-db-configuration)) → Graphiti requires a **graph** backend; CockroachDB is not a native graph DB, so it is *not* a drop-in for Graphiti's store (relevant to the "build vs plug" decision).

**Key concepts.** **Bi-temporal model** — each edge carries **four timestamps**: `created_at` / `expired_at` (transaction timeline: when the system learned/retired the fact) and `valid_at` / `invalid_at` (real-world timeline: when the fact was actually true). This lets Zep answer "what did we believe, and when was it true?" and gracefully invalidate superseded facts instead of overwriting. ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956))

**Benchmarks (numbers).**
- **Deep Memory Retrieval (DMR):** **Zep 94.8%** vs **MemGPT 93.4%**. ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956))
- **LongMemEval:** **Zep 63.8%** vs **Mem0 49.0%** (GPT-4o); up to **+18.5% accuracy** and **~90% latency reduction** vs baseline. ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956), [Vectorize](https://vectorize.io/articles/mem0-vs-zep))

**Strengths.** Best-in-class temporal reasoning; enterprise data + chat fusion; strong LongMemEval numbers; incremental updates. **Weaknesses.** Requires and is coupled to a graph DB; more operationally complex; the graph layer adds ingestion cost/latency; benchmark claims come from the vendor itself.

---

## 3. Letta (formerly MemGPT)

**Architecture.** **OS-tiered / virtual-context.** Descends from the MemGPT paper's "LLM-as-OS" idea: manage a fixed context window like RAM and page data to/from external storage like disk. Memory is organized as named **memory blocks**. ([Letta: agent memory](https://www.letta.com/blog/agent-memory/), [Letta teardown](https://kenhuangus.substack.com/p/how-ai-agents-actually-remember-inside))

**Memory types.** **Working** (in-context core blocks: `persona`, `human`), **archival** (long-term searchable facts — semantic), **recall** (full conversation history — episodic). Blocks have token caps; when full the agent decides what to evict/summarize. Procedural behavior emerges via self-editing blocks.

**Backing store (critical).** **PostgreSQL + pgvector for everything** — memory blocks, conversation history, embeddings, agent metadata — with hybrid vector + full-text search fused via reciprocal rank fusion. Letta's stated rationale: Postgres is transactional, "good enough," and developers already know it. ([Letta teardown / Ken Huang](https://kenhuangus.substack.com/p/how-ai-agents-actually-remember-inside)) → **This is the single most CockroachDB-relevant framework: it is Postgres-native. CockroachDB speaks the Postgres wire protocol, so it is a natural candidate to replace single-node Postgres for multi-region/HA deployments** (pgvector-equivalent vector support is the compatibility item to verify).

**Key concepts.** **Memory blocks** (shared, addressable, token-bounded units of context); **self-editing memory** (agent calls tools to rewrite its own core memory); **sleep-time compute** — a background "sleep-time agent" runs asynchronously on the *same* blocks while the primary agent is idle, abstracting patterns, resolving contradictions, and pre-computing associations. ([Letta: agent memory](https://www.letta.com/blog/agent-memory/), [SurePrompts walkthrough](https://sureprompts.com/blog/letta-memgpt-walkthrough))

**Benchmarks (numbers).** On **DMR**, MemGPT (Letta's lineage) is the baseline Zep beats **94.8% vs 93.4%**. Letta emphasizes agent-design and stateful serving over benchmark-leaderboard chasing. ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956))

**Strengths.** Clean mental model (RAM/disk); self-editing + sleep-time compute; Postgres-native and self-hostable; full agent runtime/server, not just a memory lib. **Weaknesses.** Heavier (a whole agent server); DMR is now an easy/saturated benchmark; no native temporal-graph reasoning.

---

## 4. LangMem (LangChain)

**Architecture.** A **lightweight, storage-agnostic SDK** that adds long-term memory to any agent. It is a thin wrapper providing memory *tools* (`manage_memory`, `search_memory`) plus background consolidation; the durable layer is a **LangGraph store** that saves JSON docs organized by **namespace + key**. ([LangChain: LangMem launch](https://www.langchain.com/blog/langmem-sdk-launch), [DigitalOcean tutorial](https://www.digitalocean.com/community/tutorials/langmem-sdk-agent-long-term-memory))

**Memory types.** Explicitly three: **semantic** (facts/preferences), **episodic** (past interactions — "how" a problem was solved), and **procedural** (self-updated system prompts — LangMem is notable for letting the agent **rewrite its own prompt**). ([LangMem memory API](https://langchain-ai.github.io/langmem/reference/memory/))

**Backing store (critical).** **Storage-agnostic by design** — any backend that can persist entries and do semantic search (embeddings). Ships against LangGraph's `BaseStore`; the reference production store is the **LangGraph Postgres store** (`AsyncPostgresStore`, with pgvector). ([LangChain long-term memory docs](https://docs.langchain.com/oss/python/langchain/long-term-memory)) → **Because the store is a pluggable Postgres-backed interface, CockroachDB is a candidate to back LangGraph's store** (subject to pgvector/index compatibility).

**Key concepts.** Namespaces for multi-tenant isolation; "hot path" (in-context) vs background memory formation; procedural prompt optimization.

**Benchmarks (numbers).** No standard LongMemEval/LoCoMo leaderboard entry as a system; it is an SDK/pattern rather than a benchmarked end-to-end memory service.

**Strengths.** Minimal, composable, framework-agnostic; only one here with first-class **procedural** (self-editing prompt) memory; trivial to swap stores. **Weaknesses.** Not a turnkey service; no built-in temporal graph; performance depends entirely on the chosen store and your wiring.

---

## 5. MemoryOS

**Architecture.** **OS-inspired hierarchical memory.** An academic system (EMNLP 2025 Oral, arXiv:2506.06326) with four modules — **Storage, Updating, Retrieval, Generation** — over **three tiers**: **short-term**, **mid-term**, and **long-term personal** memory. ([arXiv:2506.06326](https://arxiv.org/abs/2506.06326), [GitHub BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS))

**Memory types.** Working (short-term), episodic (mid-term dialogue segments), and long-term semantic/persona ("long-term personal memory"). Updates flow **short→mid** via a dialogue-chain **FIFO** and **mid→long** via a **segmented "page" organization** strategy — a direct analogy to OS paging. ([Emergent Mind: MemoryOS](https://www.emergentmind.com/topics/memoryos))

**Backing store (critical).** Research/reference implementation is a self-contained Python system persisting to **local files / embedded storage** (JSON + embeddings) rather than mandating an external DB; it is not built around a pluggable enterprise store. → For production/multi-region durability its store layer would need to be swapped for a real DB — **an opening to put its tiered logic on CockroachDB.**

**Key concepts.** OS memory hierarchy; heat/priority-based promotion between tiers; segmented page organization.

**Benchmarks (numbers).** On **LoCoMo** (GPT-4o-mini), reports **+49.11% F1** and **+46.18% BLEU-1** over baselines. ([arXiv:2506.06326](https://arxiv.org/abs/2506.06326))

**Strengths.** Principled, well-cited tiered design; strong LoCoMo deltas; clear promotion/eviction semantics. **Weaknesses.** Research-grade, not a hardened product; storage layer not enterprise-pluggable out of the box; LoCoMo-only evidence.

---

## 6. Cognee

**Architecture.** **Hybrid graph + vector "memory engine."** Core is the **ECL pipeline — Extract, Cognify, Load**: ingest data in any format, use an LLM to extract entities/relationships, and load them into a **unified graph + vector store**. Exposes ~14 retrieval modes (classic RAG → chain-of-thought graph traversal). ([Cognee: how it builds AI memory](https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory), [GitHub topoteretes/cognee](https://github.com/topoteretes/cognee))

**Memory types.** Semantic (entity/relationship knowledge graph) + document/episodic content; retrieval-driven working memory. Temporal awareness via graph structure rather than a formal bi-temporal model.

**Backing store (critical).** **Fully pluggable, no vendor lock-in at any layer.** Three storage roles:
- **Graph:** Kuzu (default, embedded), **Neo4j**, FalkorDB, **Amazon Neptune**, Memgraph, NetworkX.
- **Vector:** LanceDB (default), **PGVector**, Qdrant, Weaviate.
- **Relational:** **SQLite (default) / Postgres**.
([Cognee graph stores](https://docs.cognee.ai/setup-configuration/graph-stores), [Cognee OSS memory frameworks](https://www.cognee.ai/blog/guides/open-source-memory-frameworks-llm-agents)) → **Cognee already supports Postgres for the relational tier and PGVector for the vector tier — both candidate CockroachDB targets** (graph tier still needs a graph DB).

**Key concepts.** ECL pipeline; "cognify" step (graph construction); unified relational+vector+graph engine with swappable backends.

**Benchmarks (numbers).** Markets accuracy/quality on internal and community RAG-vs-graph evaluations; no single canonical LongMemEval/LoCoMo headline number as authoritative as Zep's/Mem0's. (Treat vendor benchmark claims cautiously.)

**Strengths.** Most storage-flexible of the graph-based systems; unifies three storage modalities; strong OSS traction; local-first defaults. **Weaknesses.** Heavier conceptual surface (pipelines + multiple stores); benchmark story less standardized; graph tier still needs a graph DB.

---

## 7. OpenMemory MCP

**Architecture.** A **local, private memory server exposed over the Model Context Protocol (MCP)** so any MCP client (Claude, Cursor, etc.) shares one memory layer. Containerized (Docker Compose) client-server model: a **vector DB (Qdrant)** + a **FastAPI MCP backend (using Mem0 under the hood)** + a Next.js dashboard; SSE transport; per-read/write audit logs. ([Mem0: OpenMemory MCP](https://mem0.ai/blog/how-to-make-your-clients-more-context-aware-with-openmemory-mcp), [DEV writeup](https://dev.to/anmolbaranwal/how-to-make-your-clients-more-context-aware-with-openmemory-mcp-4h71))

**Memory types.** Whatever Mem0 provides underneath — primarily semantic facts/preferences with metadata; it is a **transport + local-storage packaging** of Mem0, not a new memory model.

**Backing store (critical).** **Qdrant** for vectors + **local Postgres** for relational metadata, all on-device. → The Postgres metadata tier is again a Postgres-compatible surface CockroachDB could serve; the vector tier is Qdrant-specific.

**Key concepts.** MCP-native; local-first / privacy (no data leaves the machine); cross-client memory sharing; audit logging. **Status note:** OpenMemory has been **deprecated / sunset in favor of the unified Mem0 self-hosted server**. ([DeepWiki: OpenMemory overview & migration](https://deepwiki.com/mem0ai/mem0/15.1-openmemory-overview-and-migration))

**Benchmarks (numbers).** Inherits Mem0's numbers (see §1); no separate benchmark identity.

**Strengths.** Standards-based (MCP) cross-tool memory; strong privacy story; easy local setup. **Weaknesses.** Deprecated; thin wrapper over Mem0; local-only orientation limits multi-region/enterprise durability (a gap CockroachDB directly addresses).

---

## 8. Comparison table

| Framework | Architecture | Memory types | Backing store | Temporal support | Notable benchmark | Open-source? |
|---|---|---|---|---|---|---|
| **Mem0** | Vector-first (+ optional graph `Mem0g`) | Semantic, working/episodic window | **Pluggable: 20+ vector stores incl. PGVector/Postgres, Supabase, Qdrant (default); Neo4j/Memgraph for graph** | Basic (timestamps; graph add-on) | LoCoMo 92.5; **LongMemEval 49.0%** | Yes (+ managed) |
| **Zep / Graphiti** | Temporal knowledge graph (graph-first) | Semantic + episodic + relational | **Graph DB: Neo4j / FalkorDB / Neptune / Kuzu** | **Strong — bi-temporal (4 timestamps/edge)** | **DMR 94.8%; LongMemEval 63.8%** | Yes (Graphiti OSS) + managed Zep |
| **Letta (MemGPT)** | OS-tiered virtual context; memory blocks | Working (core), archival (semantic), recall (episodic) | **Postgres + pgvector (everything)** | Via recall/timestamps; no temporal graph | DMR (MemGPT baseline) 93.4% | Yes (+ cloud) |
| **LangMem** | Storage-agnostic SDK over LangGraph store | **Semantic, episodic, procedural (self-editing prompt)** | **Pluggable LangGraph store; Postgres/pgvector reference** | Minimal | No standard system-level score | Yes |
| **MemoryOS** | OS-inspired hierarchical (short/mid/long) | Working, episodic, long-term semantic/persona | Local files / embedded (research impl.) | Tier-based recency, FIFO/paging | **LoCoMo +49.11% F1 / +46.18% BLEU-1** | Yes (research) |
| **Cognee** | Hybrid graph + vector (ECL pipeline) | Semantic KG + document/episodic | **Pluggable: Kuzu/Neo4j/FalkorDB/Neptune (graph); LanceDB/PGVector/Qdrant (vector); SQLite/Postgres (relational)** | Graph-structural (no formal bi-temporal) | Internal/community RAG evals | Yes (+ cloud) |
| **OpenMemory MCP** | Local MCP memory server (Mem0 inside) | Inherits Mem0 (semantic) | **Qdrant (vectors) + local Postgres (metadata)** | Inherits Mem0 | Inherits Mem0 | Yes (**deprecated**) |

*Benchmark note:* LongMemEval GPT-4o figures (Zep 63.8% vs Mem0 49.0%) come from Zep's paper and independent write-ups and are contested by Mem0; LoCoMo is now considered near-saturated/weak, and **BEAM** (ICLR 2026, 100K–10M-token conversations) is the emerging "unsaturated" benchmark. ([BEAM](https://github.com/mohammadtavakoli78/BEAM), [Mem0: 2026 benchmarks](https://mem0.ai/blog/ai-memory-benchmarks-in-2026), [Zep critique](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/))

---

## 9. Framework vs Database — where does CockroachDB fit?

**These frameworks are not, for the most part, competitors to CockroachDB.** They are **memory *logic*** — extraction, consolidation, conflict resolution, tiering, temporal reasoning, retrieval. That logic is stateless-ish; it must **persist to a durable store**. The durable store is the layer that needs to be **consistent, highly available, horizontally scalable, and multi-region** — i.e., CockroachDB's core value proposition. Two strategic plays:

### Play A — Be the backend *underneath* an existing framework
CockroachDB speaks the **PostgreSQL wire protocol and SQL dialect**, so any framework whose durable tier is Postgres/pgvector-shaped is a candidate integration target:

- **Letta (MemGPT)** — **strongest fit.** It is Postgres+pgvector-native for *everything* (blocks, history, embeddings, metadata). Swapping single-node Postgres for CockroachDB gives it multi-region HA and horizontal scale. The one compatibility item to validate is **vector indexing** (pgvector operators/index types) on CockroachDB.
- **LangMem** — storage-agnostic; its reference production store is the **LangGraph Postgres store**. CockroachDB can back that store (validate vector search support).
- **Mem0** — pluggable with explicit **PGVector/Postgres** and Supabase backends. CockroachDB is a candidate for the relational/vector tier (again, vector-index compatibility is the check).
- **Cognee** — pluggable relational (**Postgres**) and vector (**PGVector**) tiers can point at CockroachDB; its **graph** tier still needs a graph DB.
- **OpenMemory MCP** — uses local **Postgres** for metadata (vectors in Qdrant); the metadata tier could sit on CockroachDB, though the project is deprecated.
- **Zep / Graphiti** — **weakest fit for a drop-in**: Graphiti is coupled to a **graph database** (Neo4j/FalkorDB/Neptune/Kuzu). CockroachDB is not a native property-graph engine, so it can't transparently replace that tier.

**Common gap these frameworks expose:** most default to single-node stores (SQLite, local Postgres, embedded Kuzu/LanceDB, one Qdrant, one Neo4j). None of those give **strongly-consistent, survivable, geo-distributed** memory out of the box. That is precisely the durability/consistency/multi-region gap CockroachDB fills — the pitch is "your agent's memory shouldn't live on one box."

### Play B — Build the memory logic *directly* on CockroachDB (skip the extra layer)
Nothing here is exotic. The primitives these frameworks implement can be built natively on CockroachDB, collapsing the stack:
- **Semantic/vector memory** → vector column + similarity search in SQL.
- **Episodic/recall** → append-only, timestamped rows (CockroachDB's distributed SQL + time-travel/AS OF SYSTEM TIME is a natural fit for recency and history).
- **Temporal / bi-temporal reasoning** (Zep's differentiator) → model the four-timestamp edge pattern (`created/expired`, `valid/invalid`) directly as SQL columns with indexes; **you get bi-temporal semantics without a separate graph DB**, plus multi-region consistency Neo4j doesn't natively provide.
- **Tiering / consolidation** (Letta, MemoryOS) → background jobs / changefeeds promoting rows between "hot" and "cold" tables.
- **Multi-tenant isolation** (LangMem namespaces) → row-level tenancy + **REGIONAL BY ROW** for data residency.

The upside of Play B is **one system instead of three** (no separate vector DB + graph DB + relational DB to operate and keep consistent) with global consistency and survivability built in. The trade-off is that you re-implement the memory logic these frameworks give for free.

**Bottom line:** position CockroachDB as the **durable, consistent, multi-region system-of-record for agent memory** — either the store these frameworks plug into (Letta/LangMem/Mem0/Cognee via Postgres-compat) or the single foundation on which to build memory logic directly, including bi-temporal reasoning that would otherwise require a dedicated graph database.

---

## Sources

- Mem0 research & LoCoMo — https://mem0.ai/research
- Mem0 vector DB overview (pluggable backends) — https://docs.mem0.ai/components/vectordbs/overview
- Mem0 storage backends (DeepWiki) — https://deepwiki.com/mem0ai/mem0/5-vector-stores
- Mem0 × Qdrant — https://qdrant.tech/documentation/frameworks/mem0/
- Mem0 vs Zep (LongMemEval numbers) — https://vectorize.io/articles/mem0-vs-zep
- Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv:2501.13956) — https://arxiv.org/abs/2501.13956
- Graphiti README / GitHub — https://github.com/getzep/graphiti/blob/main/README.md
- Graphiti Neo4j / FalkorDB / Kuzu config — https://help.getzep.com/graphiti/configuration/neo-4-j-configuration , https://help.getzep.com/graphiti/configuration/falkor-db-configuration , https://help.getzep.com/graphiti/configuration/kuzu-db-configuration
- Zep: "Is Mem0 really SOTA?" (LoCoMo critique) — https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/
- Letta: agent memory (blocks, sleep-time compute) — https://www.letta.com/blog/agent-memory/
- Letta teardown (Postgres+pgvector backing) — https://kenhuangus.substack.com/p/how-ai-agents-actually-remember-inside
- Letta walkthrough — https://sureprompts.com/blog/letta-memgpt-walkthrough
- LangMem SDK launch — https://www.langchain.com/blog/langmem-sdk-launch
- LangMem tutorial (DigitalOcean) — https://www.digitalocean.com/community/tutorials/langmem-sdk-agent-long-term-memory
- LangChain long-term memory docs — https://docs.langchain.com/oss/python/langchain/long-term-memory
- MemoryOS (arXiv:2506.06326) — https://arxiv.org/abs/2506.06326
- MemoryOS GitHub — https://github.com/BAI-LAB/MemoryOS
- Cognee: how it builds AI memory — https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory
- Cognee graph stores (backends) — https://docs.cognee.ai/setup-configuration/graph-stores
- Cognee GitHub — https://github.com/topoteretes/cognee
- OpenMemory MCP — https://mem0.ai/blog/how-to-make-your-clients-more-context-aware-with-openmemory-mcp
- OpenMemory overview & deprecation (DeepWiki) — https://deepwiki.com/mem0ai/mem0/15.1-openmemory-overview-and-migration
- BEAM benchmark (ICLR 2026) — https://github.com/mohammadtavakoli78/BEAM
- Mem0: AI memory benchmarks 2026 (LoCoMo/LongMemEval/BEAM) — https://mem0.ai/blog/ai-memory-benchmarks-in-2026
