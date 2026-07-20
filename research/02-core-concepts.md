# 02 — Core Concepts (the SOTA vocabulary)

Everything a teammate needs to speak fluently about agentic memory in 2026. Skim the bold terms.

## 1. The memory-type taxonomy

Production agent memory is not one thing. The field has converged on four types:

- **Working memory** — the live scratchpad for the current session/turn (context window + short-term
  state). Fast, ephemeral.
- **Episodic memory** — *what happened*: a log of past events/interactions ("on July 3 the user
  reported a login bug").
- **Semantic memory** — *facts / knowledge*: distilled, durable truths ("the user's org uses
  Postgres 15").
- **Procedural memory** — *how to do things*: learned workflows, coding patterns, tool-use habits,
  review conventions, runbooks, deployment steps. **Underdeveloped in tooling → biggest opportunity.**

A production system continuously **extracts** durable memory from raw episodes and **consolidates**
it into semantic/procedural stores.

Source: [Mem0 — State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)

## 2. Two dominant architectures

- **Vector + (optional) graph** — e.g. **Mem0**. A vector store does semantic search; an optional
  knowledge graph captures entity relations. Bets on **breadth**.
- **Temporal knowledge graph** — e.g. **Zep / Graphiti**. Everything is a graph where **time is a
  first-class dimension** (bi-temporal edges: when a fact was true *and* when we learned it). Nodes =
  Episodic / Entity / Community; edges are bi-temporal. Bets on **depth**.

**Benchmark reality (LongMemEval, GPT-4o):** Zep **63.8%** vs Mem0 **49.0%** — the gap concentrated in
**knowledge-update and temporal-reasoning** questions. But the graph path costs more: Mem0's own paper
found its graph variant ran search ~3× slower and cost ~2× tokens vs plain vectors.

**Takeaway for us:** bi-temporal modeling is where accuracy lives, and CockroachDB's relational +
JSONB + vector store lets us build a **temporal, bi-temporal-aware memory** in *one* consistent
database instead of stitching Neo4j + a vector DB together.

Sources: [Zep: A Temporal KG Architecture for Agent Memory (arXiv 2501.13956)](https://arxiv.org/abs/2501.13956),
[Mem0 vs Zep comparison](https://vectorize.io/articles/mem0-vs-zep)

## 3. Sleep-time compute / memory consolidation

Coined by **Letta** (April 2025). Instead of sitting idle between tasks, an agent uses "downtime" to
**reprocess raw context into learned context** — a **dual-agent model**: a live agent handles
interactions; a **sleep-time agent** activates during downtime to analyze past conversations, parse
documents, and reorganize memory blocks. Inspired by how humans consolidate memories during sleep.
This is exactly how you *manufacture* good procedural/semantic memory from noisy episodes.

**Why it matters for us:** a background consolidation job (AWS Lambda / a scheduled worker) that reads
raw episodes from CockroachDB and writes distilled, deduplicated, higher-confidence memories back is
a concrete, demoable, on-trend feature.

Sources: [Letta — Sleep-time Compute](https://www.letta.com/blog/sleep-time-compute/),
[Letta Docs — Sleeptime agents](https://docs.letta.com/guides/agents/architectures/sleeptime/)

## 4. Multi-scope memory & governance

The emerging enterprise pattern: every memory write is **tagged with identity scopes** —
`user_id` (cross-session personal facts), `agent_id` (per-agent facts), `session_id`/`run_id`
(conversation-scoped), and `org_id`/`app_id` (**shared organizational context**).

**Governed shared memory** is becoming a foundational infra layer: organizations must show **full
provenance** for any autonomous decision, with **signed, tamper-proof audit records** as primary
compliance artifacts under the **EU AI Act** and **NIST RMF**. Today's frameworks fail here —
inter-agent memory is unsigned, volatile, admin-rewritable.

Sources: [Governed Shared Memory for Multi-Agent LLM Systems (arXiv 2606.24535)](https://arxiv.org/html/2606.24535),
[Multi-agent memory architectures](https://zylos.ai/research/2026-03-09-multi-agent-memory-architectures-shared-isolated-hierarchical/)

## 5. Benchmarks (how "good memory" is measured in 2026)

| Benchmark | What it tests | Frontier score |
|-----------|---------------|----------------|
| **LoCoMo** | Long conversational memory | 92.5 |
| **LongMemEval** | Temporal, multi-hop, knowledge-update retrieval | 94.4 |
| **BEAM (1M tokens)** | Very long-horizon memory | 64.1 |
| **BEAM (10M tokens)** | Extreme long-horizon (staleness cliff) | 48.6 |
| **DMR** | MemGPT's dialogue memory metric | Zep 94.8 / Mem0 93.4 |

Biggest recent gains: **temporal reasoning (+29.6)** and **multi-hop reasoning (+23.1)** — the
categories that matter for real histories where facts accumulate, change, and relate over time.

Source: [Mem0 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)

## 6. Six production requirements (hard-won, 18 months of deployments)

1. **Async memory writes** (never block the response path).
2. **Reranking** beyond raw vector similarity.
3. **Metadata filtering** for scoped queries across contexts.
4. **Timestamp preservation** during migrations (temporal ordering must survive).
5. **Configurable memory depth + exclusion rules** per domain.
6. **Structured error codes**, not opaque failures.

These are our **production-readiness checklist** — implementing them visibly is worth judging points.

## 7. Frontier research to name-drop (shows we're current)

MAGMA (multi-graph agentic memory), EverMemOS (self-organizing memory OS), Memoria (scalable memory
for conversational AI), MemoryOS (OS-inspired tiered memory), LangMem (LangChain), MemGuard (memory
contamination defense), TiMem (temporal-hierarchical consolidation).

Sources: [Awesome-Memory-for-Agents](https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents),
[Memory in the Age of AI Agents: A Survey](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)
