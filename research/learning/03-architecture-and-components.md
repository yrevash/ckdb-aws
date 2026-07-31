# 03 — Architecture & components (a tour of the code)

## The system in one diagram

```
                        ┌───────── AWS (compute) ─────────┐
  SRE  ⇄  Web Console  ⇄  Responder Agent (Strands/Fargate)
                              │  │  │
              recall (MCP RO) │  │  │ reason        act+record (SQL, RW)
                              ▼  ▼  ▼
                       Bedrock (Sonnet/Haiku/Titan + Guardrails)
                              │
     ┌──────────── CockroachDB — ONE STORE (multi-region, SURVIVE REGION) ───────────┐
     │  memory: episodic · semantic(bitemporal) · procedural(runbooks)  [VECTOR+C-SPANN]│
     │  operational: services · deploys · incidents · orders · remediation_actions      │
     └────────────┬────────────────────────────────────────────────────────────────────┘
                  │ changefeed (on resolve)
                  ▼
   API Gateway → receiver Lambda → SQS → consolidator Lambda → distils facts+runbooks → back to memory
                  
   System-Under-Management simulator injects faults ⇄ the agent's actions mutate operational tables
```

**The key architectural idea:** memory and operational data are **co-located** in CockroachDB so the
act+record write is one transaction (the wedge). Recall reads via MCP (read-only role); the write goes
via direct SQL (writer role). Consolidation is a separate, async, event-driven path.

## Folder-by-folder tour

The repo is a monorepo. Here's what each folder is, how it works, and why it exists.

### `backend/` — the agent and its API (Python)
The heart. Contains:
- **The responder** — the perceive→recall→reason→act→record loop (`service.py`, `runtime.py`).
- **Adapters** (`adapters/`) — the pluggable implementations: `bedrock.py` (LLM/embeddings),
  `cockroach.py` (the atomic SQL write path), `mcp.py` (read recall), `recall.py` (the three-stage
  recall logic), `outcome.py` (recording results), and `fakes.py` (test doubles so it runs with **no
  AWS credentials** locally).
- **`guardrails/`** — the security controls (allowlist, provenance gate, injection screening, input
  validation, role-scoping). See file 06.
- **`api.py` / `transport.py`** — the HTTP + SSE (server-sent events) API that streams events to the
  console.
- **`ports.py` / `domain.py`** — the interfaces and typed domain objects (so tools are typed, never
  free-text — a security property).
- **Why the "ports & adapters" shape?** It lets the same agent logic run against *fakes* locally
  (fast, no cloud) and against *real Bedrock/CockroachDB* in production, by swapping adapters. That's
  why you can run the whole thing on your laptop.

### `db/` — the database schema (SQL)
- **`migrations/`** — the schema, applied in order: `0002_core_schema` (all tables), `0003_memory_indexes`
  (the C-SPANN vector indexes), `0004` (episodic retention/TTL), `0005` (recall provenance/scope),
  `0006_bitemporal_transitions` (facts-evolve), `0007_audit_logging` (audit + reader/writer/consolidator
  roles).
- **`bootstrap/`** — one-time cluster setup that isn't a schema migration: `001_enable_cspann` (turn on
  vector indexing), `090_cluster_settings` (audit + rangefeed settings, applied post-migration),
  `010_multiregion` (the SURVIVE REGION FAILURE config for the resilience cluster).
- **`queries/`** — the important SQL: `rollback_and_record.sql` (the one-transaction wedge),
  `recall_*.sql` (the recall queries).
- **`apply.sh`** — applies bootstrap + migrations idempotently.
- **Why split bootstrap from migrations?** Cluster settings need admin privileges the migration
  identity shouldn't have — a security principle enforced by a test.

### `simulator/` — the fake world the agent operates on (Python)
- **`conductor.py` / `models.py`** — a **deterministic** "System-Under-Management" (SUM): a mock
  microservices platform with services, deploys, incidents, orders. It **injects faults** (10 incident
  families + 2 temporal-drift families) so the agent has realistic incidents to respond to, and the
  agent's actions actually mutate this world. Deterministic = reproducible (same seed → same run), which
  is what makes the A/B evaluation fair.
- **Why?** You can't test an SRE agent against real 3am outages. The simulator is a controllable,
  repeatable outage generator.

### `evaluation/` — proving memory works (Python)
- **`runner.py` / `responders.py`** — runs the **A/B experiment**: the *same* incident stream through
  two agents — one **with memory**, one **cold-start** (no learned memory) — and measures the
  difference (MTTR, recall@k, wrong actions, orders saved, temporal-validity). Emits a machine-readable
  scorecard (`evaluation/reports/phase2.json`).
- **Why?** "Memory helps" is a claim; this turns it into numbers (−63.6% MTTR, etc.).

### `resilience/` — proving RPO=0 (Python)
- **Probes** (`probes/`) for RPO, RTO, read-your-writes freshness, cross-agent visibility, atomicity;
  a **harness** that brings up the 9-node multi-region cluster, **kills a region**, and measures. Emits
  `evaluation/reports/phase3-resilience.json`.
- **Why?** The "never goes down" claim needs real telemetry from an actual region-kill, not a slide.

### `consolidation/` — the sleep-time job (Python)
- **`pipeline.py` / `handlers.py` / `grouping.py` / `model.py`** — the Lambda logic that reads resolved
  incidents (grouped idempotently), distills them into facts + runbooks via Bedrock, and writes them
  back with provenance.
- **Why separate from backend?** It runs async on a different trigger (a database changefeed → SQS →
  Lambda), never blocking the live agent.

### `web/` — the incident console (Next.js / TypeScript)
- **`components/`** — the console: the 3-rail "Investigation" view (incident feed · ChatOps · memory
  timeline), the **Resilience** "failover theater" (RPO=0 counter holding through a region kill), and
  the **Temporal drift** view. `lib/` has the data mappers + replay fixtures; `hooks/` fetch live data
  with a camera-safe replay fallback.
- **Why?** The demo has to make "memory is load-bearing" *visible* — the memory timeline shows exactly
  what the agent recalled and why.

### `infra/` — AWS deployment as code (Python CDK)
- **`postmortem_infra/stacks.py`** (network, KMS, secrets, guardrail, Fargate app, WAF),
  **`consolidation_stack.py`** (SQS + Lambdas), **`security_stack.py`** (CloudTrail/GuardDuty/Config).
  All security-tested (`infra/tests/test_security.py`). See file 06.
- **Why CDK?** Reproducible, reviewable, and security properties are *tested* on the synthesized
  CloudFormation — no click-ops.

### `docs/` and `research/`
- **`research/`** — the thinking: `postmortem/` (design), `deep-dive/` (competitive research),
  `learning/` (this folder).
- **`docs/`** — the doing: implementation phases, architecture, demo script, hardening, `security/`
  (the full security posture), and session notes.

## How they connect at runtime (local vs production)

- **Locally** (what you can run today): CockroachDB in Docker + the backend on the **fake runtime**
  (Bedrock/MCP replaced by deterministic doubles). Everything runs without AWS credentials, and the
  verifiers prove it.
- **Production** (Aug 1): the same backend swaps in real Bedrock + Managed MCP + CockroachDB Cloud,
  deployed by the CDK onto ECS Fargate + Lambda, with the security controls live.
