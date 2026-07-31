# 02 — Core concepts (the vocabulary)

Everything you need to speak fluently about the project. Each concept: what it is, why it matters here.

## Agentic memory — the four types

"Memory" for an AI agent isn't one thing. The field (and our schema) splits it into four:

- **Working memory** — the live scratchpad for the current task (the current conversation/context).
  Throwaway.
- **Episodic memory** — *what happened*: a log of events. "On July 3, checkout latency spiked after
  deploy #5120; we rolled back to #5119 and it recovered." In our DB: the `episodic_events` table.
- **Semantic memory** — *facts*: distilled, durable truths. "checkout-api's safe rollback target is the
  previous stable deploy." Table: `semantic_facts`.
- **Procedural memory** — *how to do things*: learned workflows / **runbooks**. "When error-rate onset
  correlates with a canary deploy, roll the canary back." Table: `procedural_memory`. **This is the
  under-built frontier** — most agent-memory tools do episodic + semantic well but barely do
  procedural. We made it a first-class, structured thing (a runbook has a *matching* surface, an
  *executable* surface with safety flags, and a *track record* of success).

## The one-transaction wedge (the single most important idea)

Normally an agent would: (a) do the action in one system, then (b) write "I did X" to a memory store.
If it crashes between (a) and (b), the two disagree — the action happened but the memory says it didn't
(or vice-versa). Because Postmortem keeps **memory and operational data in the same CockroachDB**, it
does both in **one transaction**:

```
BEGIN;
  UPDATE deploys ...           -- the real remediation (activate #5119, retire #5120)
  INSERT remediation_actions   -- the audit record
  INSERT episodic_events ...   -- the memory of the action
COMMIT;                        -- all three, or none. They can never disagree.
```

You'll see this on the console as the **"Transaction Envelope."** This is the thing no separate
vector-DB architecture can offer.

## Vector search & C-SPANN (how "recall" works)

To find "similar past incidents," we turn text into a list of numbers (an **embedding** — a
1024-dimension vector, produced by AWS's **Titan Text Embeddings V2** model). Similar incidents have
similar vectors. Finding the nearest vectors fast is **vector search** (a.k.a. approximate
nearest-neighbor, ANN).

**C-SPANN** is CockroachDB's distributed vector index (shipped in v25.2). The magic: the vectors live
*inside the same SQL database* as the operational data, indexed with `CREATE VECTOR INDEX`. So "find
similar incidents scoped to this org" is a plain SQL query — no separate Pinecone to keep in sync. We
use the **cosine** distance metric and store embeddings unit-normalized.

## MCP vs direct SQL (the read/write split)

- **MCP (Model Context Protocol)** — a standard way for AI agents to talk to tools/data. CockroachDB
  offers a **Managed MCP Server** that's **read-only by default**, audited, and RBAC-scoped. Postmortem
  uses it for the **recall** path (reading memory safely).
- **Direct SQL** — the **act+record** write path goes through a normal pooled SQL connection under a
  *writer* role, because MCP is stateless and can't hold a multi-statement transaction (and the wedge
  requires one). So: **recall via MCP (read), act via SQL (write)** — and those are two different
  least-privilege database roles.

## Bitemporal facts (facts evolve, they don't get overwritten)

The world changes: a fix that worked last year can be *wrong* today (e.g. after a platform migration).
So semantic facts are **bitemporal** — each fact records two timelines:
- **valid time** (`valid_from` / `valid_to`): when the fact was true in the real world.
- **system time** (`recorded_at`): when we learned it.

When a fact changes, we don't overwrite it — we **close** the old one (`valid_to` set, `superseded_by`
pointer) and **open** a new one. The agent always recalls the *currently-valid* fact for the incident's
time, but the full history is preserved for audit. This is why the console has a "facts evolve, not
overwrite" view.

## Sleep-time consolidation (the agent gets smarter overnight)

Raw incidents are noisy. **Consolidation** is a background job (an AWS Lambda, triggered when incidents
resolve) that reads the raw episodes and **distills** them into clean semantic facts and reusable
procedural runbooks — deduplicating, scoring confidence, and recording provenance. Inspired by how
humans consolidate memories during sleep. It only creates a runbook from *successful, provenance-bearing*
episodes, so bad outcomes don't become "best practice."

## RPO=0 / RTO<10s & multi-region survival

- **RPO** (Recovery Point Objective) = how much data you can lose. **RPO=0** = zero committed data lost,
  ever. CockroachDB guarantees this by writing every commit to a quorum of replicas across regions
  *before* telling the client "done."
- **RTO** (Recovery Time Objective) = how long until service is restored after a failure. We measured
  **<10s** (actually 0.045–0.099s) for automatic recovery when a whole region is killed.
- **SURVIVE REGION FAILURE** = a CockroachDB setting where the data is spread so a full region can die
  and reads *and* writes keep working, automatically, no human failover. We prove this locally on a
  9-node, 3-region cluster.

## The stack in one glance

| Layer | Choice | Why |
|-------|--------|-----|
| Memory + operational store | **CockroachDB v26.2** | the wedge (single store, RYW, RPO=0) |
| Vector index | **C-SPANN, `VECTOR(1024)`, cosine** | vectors live with the data |
| Embeddings + reasoning | **AWS Bedrock** — Titan V2 (embeds), Claude Sonnet 4.6 (reason), Haiku (cheap/volume) | managed, strong models |
| Agent framework | **AWS Strands** | AWS-native, plain-Python tools |
| Agent hosting | **ECS Fargate** | always-on, warm DB pool, streaming |
| Consolidation | **AWS Lambda** (changefeed → SQS → Lambda) | async, event-driven |
| Console | **Next.js + shadcn** | the incident UI |
| Infra as code | **AWS CDK (Python)** | reproducible, security-tested |
