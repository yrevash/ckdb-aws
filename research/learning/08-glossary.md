# 08 — Glossary

Fast definitions for every term and acronym in the project.

## Domain / SRE
- **SRE** — Site Reliability Engineer; the on-call person Postmortem assists/automates.
- **Incident** — a production problem (outage, latency spike). Table: `incidents`.
- **MTTR** — Mean Time To Recovery; how long to resolve an incident. Our headline metric (−63.6%).
- **Runbook** — a documented procedure to fix a class of problem. Here: learned **procedural memory**.
- **Canary deploy** — releasing a new version to a small slice first; a common source of incidents.
- **SLO** — Service Level Objective; the reliability target (e.g. p99 latency < X).
- **p99 latency** — the latency the slowest 1% of requests see; a standard health signal.
- **SUM** — System-Under-Management; the mock platform the agent operates on (`simulator/`).

## Memory
- **Agentic memory** — persistent memory that makes an AI agent useful across sessions.
- **Episodic / semantic / procedural / working memory** — the four memory types (see file 02).
- **Embedding** — a list of numbers representing text's meaning; similar text → similar vectors.
- **Vector search / ANN** — finding the nearest embeddings (Approximate Nearest Neighbor).
- **Recall** — retrieving relevant memories for the current incident (the "read" path).
- **Consolidation** — the background job that distills raw incidents into facts/runbooks ("sleep-time").
- **Bitemporal** — a fact tracked on two timelines: **valid time** (true in the world) and **system
  time** (when we learned it), so facts *evolve* instead of being overwritten.
- **Provenance** — the recorded source/justification of a memory or action (what it's grounded in).

## CockroachDB
- **CockroachDB** — a distributed, PostgreSQL-compatible SQL database; the memory + operational store.
- **C-SPANN** — CockroachDB's distributed vector index (based on Microsoft SPANN). `CREATE VECTOR INDEX`.
- **VECTOR(1024)** — the embedding column type; 1024 dimensions (matches AWS Titan V2).
- **Cosine / L2 / inner-product** — vector distance metrics; we use cosine.
- **RPO** — Recovery Point Objective; data lost on failure. **RPO=0** = none.
- **RTO** — Recovery Time Objective; time to recover. We measured <10s.
- **SURVIVE REGION FAILURE** — CockroachDB config where a full region can die and reads+writes continue.
- **Read-your-own-writes (RYW)** — a write is immediately visible to subsequent reads, everywhere.
- **Serializable / linearizable** — the strongest consistency guarantees (no anomalies, instant visibility).
- **Changefeed / CDC** — a stream of database changes; triggers the consolidation Lambda.
- **PITR** — Point-In-Time Recovery; `RESTORE ... AS OF SYSTEM TIME` to undo logical corruption.
- **MCP** — Model Context Protocol; the standard agent↔tool interface. CockroachDB's **Managed MCP
  Server** is the read-only recall path.
- **ccloud** — CockroachDB Cloud's agent-friendly CLI (used for the region-disruption demo + ops).
- **Agent Skills** — CockroachDB's open-source, machine-executable expertise (used in hardening).
- **RBAC** — Role-Based Access Control; our `postmortem_reader` / `writer` / `consolidator` roles.

## AWS
- **Bedrock** — AWS's managed foundation-model service (Claude, Titan). Reasoning + embeddings.
- **Sonnet 4.6 / Haiku / Titan V2** — the specific Bedrock models (reason / cheap-volume / embeddings).
- **Bedrock Guardrails** — content filters (prompt-attack, PII) applied to model calls.
- **AgentCore Memory** — AWS's *managed* agent-memory service; the competitor we deliberately beat (it's
  not a database — no SQL/transactions).
- **Strands** — AWS's agent framework; how the responder loop + tools are built.
- **ECS Fargate** — serverless containers; hosts the always-on agent + backend.
- **Lambda** — serverless functions; runs the async consolidation job.
- **SQS** — a message queue; buffers changefeed events into the consolidator (with a DLQ).
- **DLQ** — Dead-Letter Queue; where un-processable messages go.
- **S3** — object storage; raw postmortems/artifacts.
- **KMS / CMK** — Key Management Service / Customer-Managed Key; encryption at rest.
- **Secrets Manager** — where all credentials live (never in code).
- **PrivateLink / VPC endpoint** — private network path to AWS services / CockroachDB (no internet).
- **WAF** — Web Application Firewall; protects the console.
- **CloudTrail / GuardDuty / Config** — AWS audit / threat-detection / compliance services.
- **CDK** — Cloud Development Kit; infrastructure as code (Python), in `infra/`.

## Build / process
- **Wedge** — the three CockroachDB-only advantages the app is built around (single-store, RYW, RPO=0).
- **Verifier / exit gate** — the script + criteria that prove a phase is done (`scripts/verify_phaseN.sh`).
- **Fake runtime** — test doubles for Bedrock/MCP so the app runs locally with no AWS credentials.
- **A/B evaluation** — running the same incidents with-memory vs cold-start to measure the difference.
- **STRIDE** — a threat-modeling framework (Spoofing, Tampering, Repudiation, Info-disclosure, DoS,
  Elevation-of-privilege).
- **Prompt injection** — an attack that hides instructions in untrusted text to hijack an LLM.
