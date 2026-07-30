# 00 — Context & Strategy

## The hackathon in one paragraph

CockroachDB × AWS invite us to build an **agentic application that uses CockroachDB as its
persistent memory layer, deployed on AWS**. The thesis: AI agents are moving into production
(writing code, running pipelines, diagnosing incidents) and they need **memory that never goes
down** — globally distributed, always-on, zero data loss, no maintenance windows. CockroachDB
is positioned as "the system of record for agentic memory."

Source: [Devpost — CockroachDB × AWS Hackathon](https://cockroachdb-ai.devpost.com/)

## Hard requirements (from the brief)

- **Use ≥ 2 CockroachDB tools** from:
  1. Cloud **Managed MCP Server** (`https://cockroachlabs.cloud/mcp`)
  2. **Distributed Vector Indexing** (C-SPANN)
  3. **ccloud CLI** (agent-ready)
  4. **Agent Skills Repo** (open source)
- **Use ≥ 1 AWS service**: Bedrock / Lambda / ECS / EKS / S3 / SageMaker / Bedrock Agents / any AWS service.
- **Deliverables:** public open-source repo (MIT/Apache-2.0 license visible in About), functional
  demo app URL, < 3-min public YouTube/Vimeo video demonstrating the memory layer at work, and a
  clear write-up of which CockroachDB + AWS tools we used and *how the agent actually used them*.
- **Optional but valuable:** architecture diagram, feedback on CockroachDB AI tools.

## Timeline & prizes

- **Deadline:** 19 Aug 2026, 02:30 GMT+5:30. (~29 days out as of the brief.)
- **Prizes:** $5,000 (1st) / $2,500 (2nd) / $1,250 (3rd) + blog feature + swag. $8,750 total.
- Online, public, all countries (standard exceptions).

## Judging criteria — and how we should read them

| Criterion | What they're really asking | How we win it |
|-----------|---------------------------|---------------|
| **Agentic Memory Design** | Is CockroachDB a *production-grade* memory layer, not a toy? Real state/embeddings/context/transactional data at scale? | Memory must be the *thing that makes the agent useful*, not a bolt-on cache. |
| **Technical Implementation** | Correct, safe use of the CRDB tools (vector index, MCP, ccloud). Quality engineering. | Use MCP's read-only-by-default + RBAC; real C-SPANN index; clean schema. |
| **Real-World Impact** | Would real users/workflows benefit? Meaningful, not just clever. | **This is our chosen north star.** Solve a painful, quantifiable problem. |
| **Production Readiness** | Secure, observable, scalable. Resilience, access control, failure handling. | Lean on CRDB multi-region survivability + audit logging + AgentCore isolation. |
| **Creativity & Originality** | Genuinely new idea or novel application. Insight into what makes agents different. | Use a 2026-frontier concept (procedural memory / sleep-time consolidation / governed shared memory). |

## Our team profile (drives scope)

- **People:** small team (2–4). Can split frontend / agent / infra.
- **Skills:** AI/ML core, comfortable in Python *and* TypeScript. Polyglot — pick what's best.
- **Primary optimization:** **Real-world impact / usefulness.**
- **Interest areas:** enterprise ops/support, dev-tools/coding agents, and new high-impact angles.

## Strategy notes

1. **Impact + Production-Readiness is our lane.** We will not out-flashy a demo-only team; we win
   by making the memory layer genuinely load-bearing and by showing it survives failure.
2. **Pick the CRDB tools that are hardest to fake:** C-SPANN vector index + Managed MCP + (bonus)
   ccloud for a live "provision/scale/failover" moment on camera. That directly earns Technical +
   Production-Readiness points.
3. **The 3-min video is a first-class artifact.** Whatever we build must have a single, legible
   "memory made this possible" moment (e.g., kill a region live, agent keeps remembering).
4. **Reuse the CockroachDB Agent Skills** where possible — using their open-source skills is both a
   requirement-checkbox and free production hardening, and shows we engaged with the toolchain.
