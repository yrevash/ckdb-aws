# Postmortem — Master Plan (synthesis of docs 01–06)

Status: **design complete, ready for implementation planning.** This doc reconciles the six
specialized plans into one coherent build, resolves the cross-doc conflicts, and lays out a 4-week
schedule, risks, cost, and the decisions still owned by the team.

Read the detail in: [`00-charter`](./00-charter.md) · [`01-memory`](./01-memory-architecture.md) ·
[`02-agent`](./02-agent-orchestration.md) · [`03-aws`](./03-aws-infrastructure.md) ·
[`04-crdb`](./04-cockroachdb-deployment-resilience.md) · [`05-data-eval`](./05-data-and-evaluation.md) ·
[`06-demo-ux`](./06-demo-and-ux.md).

---

## 1. Locked technical decisions (reconciled — single source of truth)

| Area | Decision | Source / notes |
|------|----------|----------------|
| Memory + operational store | **CockroachDB**, memory & operational tables **co-located** for one-txn atomicity | 01, charter wedge |
| CockroachDB version | v25.3+ (target v26.2) — needed for `vector_cosine_ops` | 04 |
| Embeddings | **Amazon Titan Text Embeddings V2**, `normalize=true`, **`VECTOR(1024)`**, `vector_cosine_ops` | 01+03+04 (see conflict #1) |
| Vector index | **C-SPANN**, prefix-scoped `(agent_id[, org_id], embedding)`; inherits REGION-survival replication | 04, 01 |
| Reasoning models | **Bedrock Claude Sonnet 4.6** (responder) + **Claude 3.5 Haiku** (classifier/consolidator) | 02, 03 |
| Agent framework | **AWS Strands Agents SDK**; LangGraph documented fallback | 02 |
| Agent hosting | **ECS Fargate** (always-on, warm CRDB pool, WebSocket/SSE streaming); AgentCore Runtime = prod upgrade | 03 |
| Consolidation compute | **AWS Lambda** (changefeed-triggered); never the interactive agent | 02, 03 |
| Changefeed path | Webhook sink → API Gateway → fast-ack Lambda → **SQS** → consolidator Lambda; `resolved`-window idempotency | 03 |
| Memory recall path | **CockroachDB Managed MCP** (read-only service account, RBAC, audit) | 02, 04 |
| Atomic write path | **Direct pooled SQL** (not MCP — MCP `insert_rows` is single-table/stateless) via a **writer service account** | 02, 04 |
| The wedge tool | `remediate_and_record` — operational mutation + episodic write in **one SERIALIZABLE txn**, provenance-gated | 02, 01 |
| Read-your-writes | **Leaseholder reads** on the recall→act hot path; follower reads for UI/analytics **only** | 01 |
| Frontend | **Next.js + shadcn (re-tokenized)** + SSE; thin visualizer over the agent's tool-event stream | 06 |
| IaC | **AWS CDK (Python)**, throwaway account, compute single-region | 03 |
| Secrets / security | Secrets Manager; per-unit least-privilege IAM; Bedrock Guardrails on both LLM paths; destructive actions human-gated | 03, 04 |
| v1 agent count | **Two agents**: live responder + background consolidator. (Detector split = stretch) | 02 |

## 2. Conflicts found & resolved (why the interface discipline paid off)

1. **Embedding dimension mismatch.** Some docs referenced `VECTOR(1536)`; Titan V2 emits **1024**.
   → **Locked at `VECTOR(1024)`.** Fix any lingering `1536` before a single embedding is written
   (changing `VECTOR(n)` later means rebuilding the column + index). *(01 already chose 1024/Titan
   V2/cosine, so 01↔03↔04 now agree.)*
2. **"ccloud scales the data tier" is not real.** `ccloud` has **no documented vCPU/node scale verb**.
   → **Charter corrected:** the agent's infra action is **not** "scale via ccloud." ccloud instead
   earns its hackathon-tool credit via the **`ccloud cluster disruption` chaos command** (failover
   demo) + backup/maintenance-window ops. vCPU scaling via the Cloud **API** is an *unverified
   stretch*, not on the critical path. We still use 4/4 CockroachDB tools (C-SPANN, MCP, ccloud,
   Agent Skills).
3. **MCP can't hold the atomic transaction.** Confirmed by 02 and 04. → Recall via MCP; **the
   memory+action write goes through direct SQL.** Two service accounts (reader/writer) make the
   recall-vs-act split a real RBAC boundary.
4. **RDS Proxy can't front CockroachDB.** → Use CockroachDB's built-in pooler + Lambda reserved
   concurrency + the warm Fargate pool.
5. **Compute region vs data region.** 03 runs compute in one region (correct — RPO=0 is
   CockroachDB's property). The failover demo (04) kills a **database** region; agent compute stays
   up and simply keeps talking to the surviving quorum. No conflict — the plans compose.

## 3. Unified architecture (end-to-end)

```
                         ┌───────────────────────── AWS (single-region compute) ─────────────────────────┐
 SRE  ⇄  Web Incident Console (Next.js/shadcn, SSE)  ⇄  Postmortem Responder Agent (Strands, ECS Fargate)
                                                             │        │              │
                                          recall (MCP, RO)   │        │ reason       │ act+record (direct SQL, RW)
                                                             ▼        ▼              ▼
                                                   Bedrock (Sonnet 4.6 / Haiku, Titan V2, Guardrails)
                                                             │
        ┌──────────────────────────── CockroachDB (multi-region, SURVIVE REGION) ───────────────────────────┐
        │  ONE STORE:  memory {episodic, semantic(bitemporal), procedural(runbooks), working}  +            │
        │              operational {services, deps, deploys, SLOs, incidents, orders, remediation_action}   │
        │              VECTOR(1024)+C-SPANN · co-located · SERIALIZABLE · leaseholder reads on hot path      │
        └───────────────┬──────────────────────────────────────────────────────────────────────────────────┘
                        │ CHANGEFEED (webhook, resolved-window)
                        ▼
     API Gateway → fast-ack Lambda → SQS → Consolidator Lambda (Haiku) → distills facts+runbooks → writes back
                        
     System-Under-Management simulator (fault-injection "conductor") mutates operational tables ⇄ agent actions
     S3: raw postmortems / seed-corpus fixture · CloudWatch/X-Ray: observability
```

## 4. The 4-week plan (deadline 19 Aug 2026)

Assumes a 2–4 person team. Roles: **INFRA** (AWS/CRDB/IaC), **AGENT** (Strands loop + memory logic +
consolidation), **DATA/EVAL** (simulator + corpus + metrics), **FE/DEMO** (console + video). On a
2-person team, pair INFRA+AGENT and DATA/EVAL+FE/DEMO.

**Week 1 — Foundations & the spine (de-risk the hardest things first)**
- INFRA: CockroachDB Cloud cluster up (start single-region to save cost); apply the `01` schema
  (memory + operational, co-located); CDK skeleton; Secrets Manager; **start the self-hosted 3-region
  EC2 cluster (failover Tier B) now** and **submit ccloud-disruption enrollment (Tier A) now**.
- AGENT: Strands responder skeleton on Fargate; Bedrock Converse wired; MCP read path + direct-SQL
  write path; the `remediate_and_record` one-txn tool proven with a hand-written incident.
- DATA/EVAL: SUM entity model + conductor v0; 2–3 incident families with recurrence.
- FE/DEMO: console shell (3 rails), SSE event stream contract agreed with AGENT.
- **Milestone: one incident handled end-to-end (perceive→recall→act→record) on a hardcoded memory.**

**Week 2 — Memory becomes load-bearing**
- AGENT: full three-stage runbook recall; procedural-memory execution with safety gates; provenance
  gate; consolidation Lambda v1 (changefeed→SQS→distill→write-back).
- DATA/EVAL: full 10-family corpus generation pipeline + S3 fixture; A/B harness (with-memory vs
  cold); recall@k + MTTR instrumentation.
- INFRA: changefeed → API GW → SQS → Lambda live; observability.
- FE/DEMO: Recall Thread + similarity dial + Transaction Envelope wired to real events.
- **Milestone: measurable with-memory vs cold-start MTTR delta on the seeded stream.**

**Week 3 — Resilience, bitemporal, polish**
- INFRA/ALL: go **multi-region** on the cluster (or cut over to the EC2 cluster) and **rehearse the
  failover demo** end-to-end; PITR backups; audit logging; Agent Skills for hardening.
- AGENT: bitemporal fact transitions; temporal-drift scenarios pass (right currently-valid fact).
- DATA/EVAL: full eval scorecard populated; freshness/atomicity/cross-agent tests green.
- FE/DEMO: freshness chip, RPO=0 counter, failover status; visual design pass per frontend-design.
- **Milestone: full dress rehearsal of the demo, metrics on screen.**

**Week 4 — Demo, video, submission**
- Record the **real off-camera region kill** (with deterministic replay harness) → the pre-recorded
  money shot; cut the ≤3-min video to the `06` storyboard.
- Freeze features; write README + architecture diagram + tool-usage writeup; add MIT/Apache license;
  make repo public; deploy the public demo URL; optional CockroachDB tools feedback.
- **Milestone: submitted, with buffer before the 19 Aug deadline.**

## 5. MVP cut lines (protect the core)

- **Must ship (the 3 wedge proofs + memory value):** perceive→recall→act→record loop; co-located
  one-txn atomicity; C-SPANN recall with recall@k≥95%; with-memory vs cold MTTR delta; the
  failover money shot (via whichever tier is ready); the console with the Recall Thread.
- **Should ship:** sleep-time consolidation; bitemporal temporal-drift scenario; audit logging.
- **Cut first if behind:** multi-agent detector split; `REGIONAL BY ROW` multi-tenant homing;
  ccloud vCPU-scaling stretch; PrivateLink (use IP-allowlist for the demo).

## 6. Risk register (top risks + mitigations)

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Failover demo tool (Tier A) enrollment doesn't land in time | High | **Tier B self-hosted EC2 cluster built from week 1** = guaranteed, rehearsable. Pre-record. |
| Multi-region Advanced cost (~$260/day) | Med | Dev on single-region/cheaper tier; only run multi-region for rehearsal + recording; `cdk destroy` / pause between sessions. |
| `langchain-cockroachdb` immaturity (fails LangChain compliance suite, async-only) | Med | We're on Strands + direct SQL/MCP, not depending on it; smoke-test any use early. |
| Live region kill on camera fails | Med | **Pre-recorded** from a real kill + replay harness (06). Never gamble live. |
| C-SPANN is "GA-but-young" (opt-in setting, merges incomplete) | Low-Med | Enable `feature.vector_index.enabled`; validate recall on our corpus early; keep dataset within tested scale. |
| Synthetic eval credibility (small N) | Low | Anchor corpus in real postmortems; human-labeled gold subset; state limitations honestly. |
| Scope creep on a 2–4 person team | Med | Enforce §5 cut lines; weekly milestone gate. |

## 7. Cost sketch (hackathon)

- **AWS:** ~$60–160/mo (Bedrock-dominated; Haiku + prompt caching to stay low).
- **CockroachDB Cloud:** single-region dev cheap; **multi-region Advanced ≈ $260/day** — budget it only
  for the rehearsal/recording window, not the whole month.
- **Self-hosted EC2 3-region (Tier B):** a handful of small instances, stopped when idle.
- **Levers:** one Fargate task (or App Runner to drop the ALB), `cdk destroy` between sessions,
  pause the Cloud cluster when idle.

## 8. Decisions still owned by the team (need your input)

1. **Budget ceiling** for the ~4 weeks — decides whether we run multi-region Advanced for more than
   the recording window, and whether we lean primarily on the self-hosted EC2 cluster (cheaper,
   fully controllable) vs the managed Cloud cluster (needed for the Managed-MCP + Agent-Skills story).
   *Recommendation:* dev + MCP story on a cheap single-region Cloud cluster; failover money-shot on
   the self-hosted 3-region EC2 cluster; only spin up multi-region Advanced if budget is comfortable.
2. **Enroll now** in CockroachDB Advanced's `ccloud cluster disruption` limited-access program? (Free
   to try; unlocks Tier-A demo. We proceed with Tier B regardless.)
3. **Accounts confirmation:** CockroachDB Cloud org + AWS account with **Bedrock model access**
   (Sonnet 4.6 + Haiku + Titan V2 enabled in the chosen region) — any blockers?

## 9. Deliverables ↔ judging map (confirming full coverage)

| Deliverable / Criterion | Covered by |
|-------------------------|-----------|
| ≥2 CockroachDB tools | **4/4**: C-SPANN, Managed MCP, ccloud (disruption/ops), Agent Skills |
| ≥1 AWS service | **Bedrock + Lambda + S3 + ECS** (+ API GW, SQS, Secrets, CloudWatch) |
| Public repo + OSS license | Week 4; MIT/Apache visible |
| Demo URL + ≤3-min video | 06 storyboard; Week 4 |
| Memory Design | 01 (3 memory types incl. procedural), bitemporal |
| Technical Implementation | one-txn wedge, real C-SPANN, MCP RBAC split |
| Real-World Impact | SRE MTTR delta, quantified |
| Production Readiness | RPO=0/RTO<10s live, audit, Guardrails, observability |
| Creativity | sleep-time consolidation + "facts evolve, not overwrite" |

## 10. Next step

Take this master plan into the **`writing-plans`** skill to produce the granular, checkpointed
implementation plan (task-by-task, per the Week 1–4 milestones), then begin execution with TDD. Before
that: **confirm the §8 decisions** (budget, enrollment, accounts).
