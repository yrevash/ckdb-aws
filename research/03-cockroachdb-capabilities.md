# 03 — CockroachDB Capabilities (what we actually get)

CockroachDB's pitch: **the system of record for agentic memory** — globally distributed, always-on,
PostgreSQL-compatible, and now natively integrated into the agent toolchain. Below is what's real and
how each piece maps to a memory system.

## The four hackathon "tools" (use ≥ 2)

### 1. Distributed Vector Indexing — **C-SPANN**
- Native ANN (approximate nearest-neighbor) vector index, GA-track from **v25.2+** (25.2 was preview).
- Based on Microsoft's **SPANN** algorithm; a **disk-based, distributed** index built to handle
  **billions of vectors** in real time.
- **RaBitQ quantization** shrinks index size by up to **94%**.
- The key differentiator: vector search lives **inside distributed SQL** — same store as your
  relational/transactional data, so **no separate vector DB**, **no reindexing pain**, and **no
  consistency gap** between embeddings and operational data. Respects geo-partitioning + survivability.
- **For memory:** store embeddings of episodes/documents next to their structured metadata and do
  semantic recall with a plain SQL query. Vector recall + transactional writes in one ACID store.

Sources: [Introducing Distributed Vector Indexing](https://www.cockroachlabs.com/blog/distributed-vector-indexing-cockroachdb/),
[C-SPANN: real-time indexing for billions of vectors](https://www.cockroachlabs.com/blog/cspann-real-time-indexing-billions-vectors/),
[How CockroachDB built vector indexing at scale](https://blog.bytebytego.com/p/how-cockroachdb-built-vector-indexing)

### 2. Cloud Managed MCP Server
- Fully hosted MCP endpoint (`https://cockroachlabs.cloud/mcp`) — **no proxy/infra to run**.
  Config snippet generated from the Cloud Console. Works natively with Claude Code, Cursor, VS Code.
- **Safe by default:** **read-only mode on by default**, writes are **opt-in via explicit consent**.
- **OAuth 2.0 + service-account API-key auth**, **RBAC** for scoped permissions, **audit logging**
  (tool name + cluster context), **system-table deny-listing**.
- **For memory:** lets an agent *explore schema, inspect query plans, run analytical recall queries*
  over the memory store safely. The read-only-default + audit trail is a **free governance story**.

Sources: [Managed MCP Server for AI Agents](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-managed-mcp-server/),
[CockroachDB for AI Agents](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-agent-ready-database/)

### 3. ccloud CLI (agent-ready)
- Redesigned with **agents as a first-class user**. Consistent noun-verb command structure,
  **JSON output on every command**, exposes the **full Cloud control plane**: provision clusters,
  backups, networking, replication.
- **Scoped service-account RBAC**; great for CI/CD and for an agent that manages its own infra.
- **For a killer demo:** an agent can **provision or scale a cluster, or trigger/observe a region
  failover on camera** — a literal "memory that never goes down" moment.

Source: [Database CLI for AI Agents](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-cli-database-automation/)

### 4. Agent Skills Repo (open source)
- Public `cockroachdb-skills` repo — small, **machine-executable** capabilities encoding CRDB
  operational expertise, following the **Agent Skills Specification**. Portable across Claude, Cursor,
  LangChain, any MCP client.
- Domains: onboarding/migration, query & schema design, operations, performance/scaling, security,
  observability. Example skills: audit user privileges, triage live SQL activity, validate production
  readiness, check backup/DR posture.
- **For us:** these are already installed in this environment (`cockroachdb:*` skills). Using them =
  requirement checkbox **+** real production hardening **+** shows toolchain engagement.

Source: [AI Agent Skills for Database Automation](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-database-lifecycle-automation/)

## Underlying platform strengths (the "never goes down" part)

- **Multi-region, strongly consistent, automatic failover.** Survives node/AZ/region loss with zero
  data loss. This is the literal answer to the hackathon thesis.
- **PostgreSQL wire-compatible** → use `pgvector`-style embeddings, existing Postgres drivers,
  SQLAlchemy/Prisma/etc. Low integration cost for a Python *or* TS stack.
- **JSONB** → flexible memory payloads (tags, scopes, provenance) alongside typed columns.
- **Changefeeds / CDC** → stream memory writes to downstream consumers in real time (e.g., trigger a
  consolidation Lambda when new episodes land).
- **Regional-by-row / geo-partitioning** → pin a user's/org's memory to their region for latency +
  data residency (relevant for the governance idea).

## Locally installed skills we can lean on (this environment)

`cockroachdb:cockroachdb-sql`, `setting-up-local-cluster`, `designing-application-transactions`,
`designing-multi-region-applications`, `analyzing-range-distribution`, `auditing-table-statistics`,
`configuring-audit-logging`, `hardening-user-privileges`, `provisioning-cluster-for-production`,
`molt-fetch/verify/replicator` (migration), and many security/ops skills. These directly support the
Production-Readiness judging criterion.

## What to double-check before committing (see `06-open-questions.md`)

- Exact **C-SPANN GA status + syntax** on the cluster version we'll use (25.2 was preview; confirm the
  current managed-cloud version and any preview flags).
- **Managed MCP write-consent** flow specifics for our agent runtime.
- Embedding dimension limits + recommended index params (RaBitQ settings).
