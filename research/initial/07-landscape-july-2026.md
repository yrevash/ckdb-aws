# 07 — Real-Time Landscape Snapshot (July 2026)

A dated snapshot of what's genuinely current *right now*, so our submission reads as 2026-native and
we don't design against stale assumptions. Captured mid-July 2026.

## The one finding that reshapes our thesis: MCP goes stateless

The **MCP 2026-07-28 specification** (release candidate out now; final ships **July 28, 2026**) is the
largest revision since MCP launched. It makes the protocol **stateless at the core** and — critically
for us — **draws an explicit line between protocol state and application state**:

> "MCP applications need memory for real workflows, but with this update, that state is handled
> **explicitly by the application** instead of hidden inside the protocol session."

**Why this matters:** the emerging standard is now *actively telling builders* that durable agent
memory belongs in an application-owned store — exactly the role CockroachDB is pitching. It also adds
a **Tasks extension** (long-running work) and **MCP Apps** (server-rendered UIs), plus OAuth 2.1 /
OIDC-aligned auth. Our narrative writes itself: *"MCP handles the wire; CockroachDB handles the
memory."*

Sources: [MCP 2026-07-28 Release Candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/),
[MCP is Growing Up (AAIF)](https://aaif.io/blog/mcp-is-growing-up/),
[Everything about MCP in 2026 (WorkOS)](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026)

## Agent-framework releases we can build on (Q2 2026)

The window **1 Apr – 5 Jul 2026** shipped more agent-framework features than any prior quarter:

- **Microsoft Agent Framework 1.0** (Apr 3) — unified successor to Semantic Kernel + AutoGen; native
  MCP + A2A, .NET and Python.
- **Anthropic Claude Agent SDK** — hierarchical subagent spawning (up to 3 levels), fallback model
  chains, community MCP tool marketplace.
- **CrewAI 1.14.6** (May 28) + June release — **pluggable memory/knowledge/RAG/flow backends**, Chat
  API, native Snowflake Cortex. (Pluggable memory backend = a clean seam to plug CockroachDB into.)
- **Pydantic AI V2** and **LlamaIndex Workflows 1.0** — both stable (Jun 22–23).

Ecosystem scale: Glama indexes **19,831+ MCP servers**; MCP Python+TS SDKs see **~97M monthly
downloads**. Early enterprise MCP adopters include Stripe, Vercel, Fortune-500 data platforms.

Sources: [Best AI Agent Frameworks 2026](https://alicelabs.ai/en/insights/best-ai-agent-frameworks-2026),
[AI Agents Stack 2026 (O'Reilly)](https://www.oreilly.com/radar/the-ai-agents-stack-2026-edition/),
[Morph — AI Agent Frameworks 2026](https://www.morphllm.com/ai-agent-framework)

## Memory ecosystem state (July 2026)

- Memory tooling now spans **21 frameworks, 20 vector stores, 3 hosting models** (managed cloud /
  self-hosted / local MCP).
- **April 2026:** a new token-efficient memory algorithm — **single-pass hierarchical extraction +
  multi-signal retrieval** — pushed benchmark frontiers (LoCoMo 92.5, LongMemEval 94.4; see `02`).
- **OpenMemory MCP** (local memory + dashboard) and **Mem0 hosted OpenMemory / cloud MCP** are the
  reference "memory-as-a-service via MCP" pattern — a pattern we could out-do on *durability +
  multi-region + governance* using CockroachDB.

Source: [Mem0 — State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026),
[Orca — MCP, A2A & Agent Context Protocols](https://orca.security/resources/blog/bringing-memory-to-ai-mcp-a2a-agent-context-protocols/)

## CockroachDB current state

- **v25.2** is the current line (the "10-year" release): **C-SPANN** distributed vector indexing,
  ~**41% efficiency gain**, **+50% throughput vs 24.3**. Vector search first shipped in **24.2**
  (preview, via pgvector); indexing matured in 25.2.
- Managed **MCP Server**, agent-ready **ccloud CLI**, and open-source **Agent Skills** repo are all
  shipped and current (see `03`).

Sources: [CockroachDB 25.2 release](https://www.cockroachlabs.com/blog/cockroachdb-252-performance-vector-indexing/),
[Vector search with pgvector](https://www.cockroachlabs.com/blog/vector-search-pgvector-cockroachdb/),
[Releases overview](https://www.cockroachlabs.com/docs/releases)

## AWS current state

- **Bedrock AgentCore** is **GA** with **12 components** incl. now-GA **episodic memory**, Gateway
  **Semantic Tool Selection** + **Web Search over MCP**, and **Policy/Guardrails**. CDK L2 stable.
  (See `04`.)

## Net implication for our build

1. Lean into the **"MCP is stateless, memory is the app's job"** narrative — CockroachDB is that app
   layer. This is *timely* and judges will recognize it.
2. Prefer a framework with a **pluggable memory backend** (CrewAI 1.14.6, or roll our own with the
   Claude Agent SDK / Strands + AgentCore) so "CockroachDB as the memory backend" is a clean, legible
   integration rather than a hack.
3. Use the **Tasks extension / long-running (8h) AgentCore Runtime** framing for the **sleep-time
   consolidation** job — it's the on-trend way to do background memory work.
