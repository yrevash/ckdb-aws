# 01 — The Problem Space

## Why agentic memory, why now

By 2026, memory is a **first-class architectural component** of agent systems — with its own
benchmark suite, its own research literature, and a measurable performance gap between good and bad
approaches. Gartner projects **40% of enterprise applications will embed AI agents by end of 2026**
(up from < 5% in 2025), and the center of gravity is shifting from single-agent apps to
**multi-agent systems** with shared state and governance.

Sources: [Mem0 — State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026),
[Enterprise multi-agent orchestration 2026](https://www.innoflexion.com/blog/multi-agent-orchestration-enterprise-genai-2026)

## The core problem (the hackathon's own framing)

> An agent whose memory goes offline doesn't degrade gracefully — **it stops.**

Traditional databases were optimized for human-scale reads/writes. Agentic systems are different:
they **spawn autonomously, write constantly, and need memory that persists across regions,
failures, and scale** with zero data loss and no maintenance windows. This is a *distributed
systems* problem, not a context-window problem.

## What's actually broken today (the gaps we can attack)

These are the documented, still-open failure modes — each is a potential product wedge:

1. **Cross-session identity resolution is unsolved.** Systems assume a stable `user_id` and fall
   apart with anonymous sessions, multi-device users, or mixed auth flows.
2. **Temporal reasoning cliffs at scale.** Benchmarks show a ~25% accuracy drop moving from 1M → 10M
   token contexts (BEAM). Agents are weak at long temporal sequences.
3. **Memory staleness / confidence rot.** A high-relevance memory can become *authoritatively wrong*
   when circumstances change (e.g., a user changes jobs), yet decay mechanisms delete low-relevance
   items instead. Change is treated as *replacement*, not *evolution* — a relocation should register
   as a transition, not an overwrite.
4. **Procedural memory is underdeveloped.** Episodic ("what happened") and semantic ("facts") are
   reasonably tooled; **procedural memory** — learned workflows, coding patterns, tool-use habits,
   review conventions, deployment/runbook steps — is architecturally supported but barely built.
   *This is the biggest open frontier for a novel submission.*
5. **No governance for shared memory.** In today's multi-agent frameworks, **inter-agent messages
   live in volatile memory, are persisted as unsigned text, and can be rewritten by an admin at any
   time.** There's no provenance, no tamper-evidence, no scoped access — even though the EU AI Act
   and NIST RMF require signed, auditable trails of autonomous decisions.

Sources: [Mem0 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026),
[Governed Shared Memory for Multi-Agent LLM Systems (arXiv)](https://arxiv.org/html/2606.24535),
[Agent-to-agent audit trail](https://truescreen.io/articles/agent-to-agent-audit-trail/)

## Why CockroachDB is unusually well-suited (not just "a database")

The gaps above are *distributed-systems* gaps — durability, consistency, geo-distribution,
survivability, auditability. That is precisely CockroachDB's home turf: it's PostgreSQL-compatible,
strongly consistent, multi-region with automatic failover, and now ships **native distributed
vector indexing** so vector recall and transactional/relational memory live in **one consistent
store** — no separate vector DB, no consistency gaps between embeddings and operational data. See
`03-cockroachdb-capabilities.md`.

## The strategic read

Because we optimize for **real-world impact**, our best wedges are (4) *procedural memory* and
(5) *governed shared memory* — both are real enterprise pain, both are 2026-frontier (creativity
points), and both map cleanly onto what CockroachDB is genuinely best at (durable, consistent,
auditable, multi-region). Those two gaps shape the ideas in `05-ideas-shortlist.md`.
