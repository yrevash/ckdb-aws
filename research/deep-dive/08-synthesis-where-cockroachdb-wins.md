# 08 — Synthesis: Where CockroachDB Wins (and Where It Doesn't)

This is the decision document. It fuses the 7 deep-dive files (`cockroachdb/01-04`,
`competitors/01-03`) into a single competitive picture with **clear metrics and goals**, so our
build deliberately showcases the axes CockroachDB actually wins on — and avoids picking a fight it
loses.

> **One-line positioning:** *CockroachDB is the only store that keeps an agent's **memory** and the
> **operational data it acts on** in one strongly-consistent, multi-region, always-on system — so the
> agent can remember, decide, and act without two systems ever disagreeing, even through a region
> failure.*

---

## 1. The competitive landscape has three tiers (and only one real fight)

The research shows "competitors" are actually three different categories, and **CockroachDB only
truly competes with one of them:**

| Tier | Examples | Are they a competitor? |
|------|----------|------------------------|
| **Memory frameworks** (logic) | Mem0, Zep/Graphiti, Letta, LangMem, MemoryOS, Cognee | **No — complements.** They implement extraction/consolidation logic and need a durable store *underneath*. CockroachDB can be that backend (Letta, LangMem, Mem0, Cognee are Postgres-compatible). |
| **Vector stores** (recall) | Pinecone, Weaviate, Qdrant, Milvus, Redis, pgvector | **Partly.** They win raw ANN speed/recall, but they're a *separate* store → the "consistency gap." |
| **Databases-as-memory** (store) | Aurora, Aurora DSQL, Neon, Yugabyte, Spanner, DynamoDB, **Bedrock AgentCore Memory** | **Yes — this is the real fight.** These are the actual alternatives for "where does the agent's memory live." |

**Implication:** we should *use* a memory framework's ideas (or build the logic ourselves) and frame
vector stores as "the thing you no longer need a separate copy of." The battle we actually pick is
**against the AWS-native memory options**, because we're forced onto AWS.

---

## 2. Master comparison matrix

Scored for the specific job of "**durable memory store for a production AI agent**." ✅ = yes/strong,
⚠️ = partial/caveated, ❌ = no.

| Capability → | **CockroachDB** | Bedrock AgentCore Memory | DynamoDB | Aurora DSQL | Aurora PG + pgvector | Postgres + pgvector | Pinecone | Qdrant/Milvus | Zep (Graphiti) |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Native vector index | ✅ (C-SPANN) | ⚠️ (managed, opaque) | ❌ (bolt-on) | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Relational + transactions | ✅ | ❌ (no SQL/txn) | ⚠️ (KV, limited txn) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Single store (vectors + operational data)** | ✅ | ❌ | ⚠️ | ❌ (no vectors) | ✅ | ✅ | ❌ | ❌ | ❌ |
| Strong consistency (read-your-writes) | ✅ (serializable, linearizable) | ⚠️ (opaque) | ❌ (eventual default) | ✅ | ✅ | ✅ | ❌ (LSN poll) | ⚠️ (bounded/stale default) | ⚠️ |
| **Multi-region auto-failover, RPO=0** | ✅ (RTO<~9s) | ⚠️ (opaque SLA) | ⚠️ (MREC, LWW) | ⚠️ (strong but no vectors) | ⚠️ (async, RPO secs) | ❌ | ⚠️ | ⚠️ (DR, RPO=0 Milvus) | ❌ |
| Horizontal write scale (no primary) | ✅ | managed | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | depends |
| Postgres-compatible (portable) | ✅ | ❌ (Bedrock lock-in) | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| AWS-native / zero-ops | ⚠️ (managed, not native) | ✅ | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| Raw ANN throughput / recall / index breadth | ⚠️ (young, L2/cos/ip) | n/a | n/a | n/a | ⚠️ | ⚠️ | ✅ | ✅ (GPU, DiskANN) | ⚠️ |
| Auto memory extraction/consolidation | ❌ (build it) | ✅ (built-in) | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ✅ |

*Sources: all figures traced in `cockroachdb/01-02` and `competitors/01-03`.*

---

## 3. Where CockroachDB genuinely wins (the uncatchable axes)

These are the columns above where CockroachDB is ✅ and **the entire rest of the row is not.** These
are the only claims we should build the demo around.

1. **Single-store consistency ("no consistency gap").** Only **CockroachDB and single-node pgvector**
   put embeddings + operational data in one ACID transaction. Every dedicated vector DB (Pinecone,
   Qdrant, Milvus, Weaviate, OpenSearch) and even MongoDB (eventually-consistent `mongot`) keep
   vectors in a *separate* store that drifts. → *"The agent's memory and the data it acts on can
   never disagree."*

2. **Read-your-own-writes at global scale.** Serializable + single-key linearizability means a memory
   an agent just wrote is **immediately** retrievable by any other agent/node — no LSN polling
   (Pinecone), no index lag (Mongo/OpenSearch), no stale-by-default reads (Milvus "Bounded",
   DynamoDB eventual). → *Directly kills the classic "I saved a memory and can't find it" agent bug.*

3. **RPO=0 region survival with automatic, no-runbook failover.** Committed writes are durable on a
   Raft quorum across regions before the client sees success; a full region can die with **zero data
   loss, zero downtime, RTO < ~9s**, self-healing (partitions heal <20s w/ v25.2 leader leases).
   AWS-native distributed options are weaker: DynamoDB global = eventual/last-writer-wins; Aurora
   Global = async (RPO in seconds); Aurora DSQL = strong but **has no vectors at all.**

4. **It's a real database, not a memory blob.** Bedrock AgentCore Memory — the strongest competitor —
   is **explicitly not a database**: no SQL, no transactions, stores conversation state only. The
   moment the agent must act on operational data (orders, tickets, balances) consistently with its
   memory, you need a real DB anyway → two systems that can disagree. CockroachDB is both.

**The union nobody else offers at once:** Postgres-compatible single-store ACID **+** horizontal
scale **+** multi-region active-active **+** native vectors. Redis has active-active but sacrifices
durability/relational; Milvus has great DR but no transactional tie to app data; pgvector has the
single-store story but only on one node.

---

## 4. Where CockroachDB loses (be honest — don't demo these)

1. **Raw ANN performance & index breadth.** Specialists (Milvus GPU/CAGRA/DiskANN, Qdrant Rust HNSW,
   Pinecone) beat a general SQL engine on QPS/p99/recall and offer more index types. C-SPANN is young
   (v25.2+, GA-but-opt-in), merges not fully implemented, **no published QPS-at-scale or head-to-head
   numbers** (only a single 14ms worked example). → **Never claim "fastest vector search."**

2. **Zero-ops convenience for a memory-only agent.** If the agent just needs chat memory with no
   serious operational data, **Bedrock AgentCore Memory is the pragmatic choice** and does auto
   fact-extraction/consolidation for free. CockroachDB there is over-engineering. → **Our use case
   must involve real operational/transactional data**, or we hand the win to AgentCore.

3. **No built-in memory cognition.** Mem0/Zep/AgentCore auto-extract facts, summarize, and manage
   temporal decay. On CockroachDB we build that logic ourselves (it's not hard, but it's not free).

4. **Closest architectural peer is Yugabyte, not an AWS product.** Open-source, Postgres-compatible,
   distributed, native HNSW vectors (tested 100M+). CockroachDB's edge over it is execution
   (multi-region ergonomics, managed cloud, AI-stack integrations), not category. (Irrelevant to
   judging — the hackathon is about CockroachDB — but good to know.)

---

## 5. The strategic wedge (how the honest weaknesses become the design brief)

Because CockroachDB **loses** the "memory-only, zero-ops" fight but **wins** the "memory + operational
data, consistent, survivable" fight, our build must live squarely in the winning zone:

> **Pick a use case where the agent must READ operational data, REMEMBER across sessions, and
> WRITE/ACT on that data — all consistently, and prove it survives a region failure.**

That single sentence is the filter for idea selection and demo design. It:
- Neutralizes AgentCore Memory (it can't hold the operational data).
- Neutralizes vector-DB speed advantages (irrelevant — we're not racing QPS; we're proving consistency).
- Forces the "one database" and "never goes down" stories to the center — exactly what the sponsor
  wants to screenshot (see `00-context-and-strategy.md` + `05-ideas-shortlist.md`).

---

## 6. Metrics & goals (what "good" looks like — make these measurable in the demo)

### A. Technical target metrics (design the system to hit these, then show them)
| Metric | Target | Why it proves our thesis |
|--------|--------|--------------------------|
| Read-your-write staleness | **0 ms** (immediately queryable after commit) | The consistency win vs eventual-consistency stores. |
| Region-failover data loss (RPO) | **0 rows** | The "never goes down" headline. |
| Region-failover recovery (RTO) | **< 10 s**, automatic, no human step | Resilience judges care about. |
| Memory + action atomicity | 1 ACID transaction spans embedding write + operational-data write | The "two systems can't disagree" win. |
| Vector recall quality | **recall@k ≥ 95%** (tune `vector_search_beam_size`) | Good enough — we compete on consistency, not raw speed. |
| Cross-agent visibility | Agent B sees Agent A's memory write with **no lag** | Multi-agent consistency story. |

### B. Demo goals (the <3-min video must show, in order)
1. Agent recalls a past memory that changes its action (memory is **load-bearing**, not a cache).
2. Memory write + operational action happen in **one transaction** (show the SQL / the atomicity).
3. **Kill a region live** → agent keeps remembering + acting, **0 data loss** (the money shot).
4. (Stretch) Two agents share memory with **immediate consistency** + an **audit trail** of who
   remembered what.

### C. Judging-criteria goals (map every build decision to a criterion)
| Criterion | Our concrete goal |
|-----------|-------------------|
| Memory Design | Use ≥3 memory types incl. **procedural** (the underbuilt frontier); memory drives real actions. |
| Technical Impl | Real C-SPANN index + Managed MCP (headless service-account) + (bonus) ccloud failover; clean schema. |
| Real-World Impact | A pain an enterprise ops/dev lead recognizes instantly; quantify the before/after. |
| Production Readiness | Show RPO=0/RTO<10s live; MCP read-only default + audit; observability. |
| Creativity | Sleep-time consolidation (Lambda + changefeed) + bitemporal "facts evolve, not overwrite." |

### D. Requirement-coverage goals (non-negotiable checkboxes)
- CockroachDB tools used (need ≥2): **C-SPANN vector index + Managed MCP Server** (core), **ccloud CLI**
  (failover demo) + **Agent Skills** (ops hardening) as bonuses → we'll use **4/4**.
- AWS services (need ≥1): **Bedrock** (reasoning/embeddings) + **Lambda** (consolidation) + **S3** (raw
  artifacts); optionally **AgentCore Runtime** to host → 2-4 services.
- Deliverables: public repo + MIT/Apache license visible + demo URL + <3-min video + written tool
  usage + architecture diagram.

---

## 7. What this means for idea selection

Filtering the three ideas from `05-ideas-shortlist.md` through the wedge in §5 (must read + remember +
act on **operational data**, and survive failure):

| Idea | Does the agent act on real operational data? | Fits the winning wedge? |
|------|:--:|---|
| **A · Postmortem (SRE incident memory)** | ✅ service state, incidents, runbooks, live metrics | **Strong fit** — memory drives real remediation actions; failover demo is native to the story ("the on-call agent's memory survives the very outage it's fighting"). |
| **B · MemGov (governed shared memory)** | ⚠️ mostly memory + audit, less operational action | Partial — great consistency/audit story, but risks looking like "memory only" unless we add a real operational workload the agents act on. |
| **C · Continuum (memory API + customer success)** | ✅ tickets, contracts, account state | Good fit, but largest scope (SDK + app). |

**Recommendation stands: lead with Idea A (Postmortem/SRE)**, because it most naturally puts the agent
in the read→remember→**act** loop on operational data *and* makes the region-failover demo
thematically perfect (the agent fighting an outage must not lose its memory during that outage). Bake
in B's **audit trail** and C's **bitemporal facts** as differentiating features.

> **Next step:** turn this into a formal design spec for "Postmortem" — schema (from
> `cockroachdb/04`), the MCP + Lambda + Bedrock architecture, and a demo script built around the
> §6 metrics. Then a step-by-step implementation plan.
